"""M4 调度器：按账号发送窗口自动触发真实发送。

只负责「哪个账号今天何时发 + 账号级租约/互斥/恢复」，具体每个好友的发送仍走
BrowserManager.run_send（SendJob 幂等键兜底、终态确认已实机验证）。

设计约定（对齐 docs/TECHNICAL_DESIGN 5.2）：
- 账号级随机取时：一账号在其 send_window 内取一个时刻，到点批量发该账号所有启用好友；
- 账号级租约：pending→leased 用条件更新原子抢占，天然保证同账号并发=1；
- 崩溃恢复：租约过期且已进入 leased/sending 的计划转 unknown_requires_review（不自动重发）；
  pending（尚未开始）可重排；
- 失败重试：failed 在当天窗口内有限次退避重试。

所有时间以 UTC 存储；窗口换算按 account.timezone。
"""

from __future__ import annotations

import asyncio
import random
from datetime import date, datetime, time, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from .core.config import Settings
from .models import (
    Account,
    AccountStatus,
    ScheduleStatus,
    SendJob,
    SendSchedule,
    JobStatus,
    utc_now,
)
from .notifications import Notifier, NullNotifier

# 账号级租约默认时长：调度点到触发发送的宽限，防另一 worker 误抢正在跑的账号
DEFAULT_LEASE_SECONDS = 5.0
# 触发后发给 run_send 的软超时（后台任务不管，仅用于租约判断），与 run_send 内部超时无关
DEFAULT_RUN_TIMEOUT = 180.0
# 失败重试退避基数（秒），指数退避：60s, 120s, ...
RETRY_BACKOFF_BASE = 60.0
RETRY_MAX_COUNT = 2
# 网络不可用时每轮延迟重试的基数（秒）
NETWORK_DEFERRAL_BACKOFF = 60.0
# 网络不可用最多连续延迟次数，超过则终态失败并通知
NETWORK_MAX_DEFERRALS = 5
# 窗口过期策略：skip=窗口已结束不再派发（防深夜打扰）；dispatch=到点即发（旧行为）
WINDOW_EXPIRY_POLICY_DEFAULT = "skip"

# 可继续调度/重排的状态
_RECOVERABLE = {ScheduleStatus.PENDING, ScheduleStatus.FAILED}
# 人工接管（绝不自动重发）
_TERMINAL = {ScheduleStatus.SUCCEEDED, ScheduleStatus.UNKNOWN_REQUIRES_REVIEW}


class Scheduler:
    """进程内异步调度器。

    server 启动时创建并 `start()`；`stop()` 时取消所有进行中的后台发送任务并关闭浏览器。
    """

    def __init__(
        self,
        engine,
        settings: Settings,
        send_account: Callable[[str], Awaitable[dict]] | None = None,
        *,
        notifier: "Notifier | None" = None,
        loop_interval: float = 60.0,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        run_timeout: float = DEFAULT_RUN_TIMEOUT,
        retry_max_count: int = RETRY_MAX_COUNT,
        retry_backoff_base: float = RETRY_BACKOFF_BASE,
        network_max_deferrals: int = NETWORK_MAX_DEFERRALS,
        network_deferral_backoff: float = NETWORK_DEFERRAL_BACKOFF,
        window_expiry_policy: str = WINDOW_EXPIRY_POLICY_DEFAULT,
        notify_on_success: bool = True,
        failure_streak_limit: int = 3,
        rng: random.Random | None = None,
        sleep=asyncio.sleep,
        clock: Callable[[], datetime] | None = None,
    ):
        self._engine = engine
        self._settings = settings
        self._send_account = send_account
        self._notifier = notifier or NullNotifier()
        self._loop_interval = loop_interval
        self._lease_seconds = lease_seconds
        self._run_timeout = run_timeout
        self._retry_max_count = retry_max_count
        self._retry_backoff_base = retry_backoff_base
        self._network_max_deferrals = network_max_deferrals
        self._network_deferral_backoff = network_deferral_backoff
        self._window_expiry_policy = window_expiry_policy
        self._notify_on_success = notify_on_success
        self._failure_streak_limit = failure_streak_limit
        self._rng = rng or random.Random()
        self._sleep = sleep
        self._clock = clock or utc_now
        self._running: set[str] = set()  # 当前正在发送的 account_id（进程内防并发的第二道闸）
        self._paused = False
        self._task: asyncio.Task | None = None
        self._send_tasks: set[asyncio.Task] = set()
        self._stopping = False
        # M6-2 重试/延迟状态（进程内）：重启即清零，重新计数；
        # 发送幂等由 SendJob 唯一键兜底，重复派发不会导致重复发送。
        self._retries: dict[str, int] = {}
        self._next_attempt_at: dict[str, datetime] = {}
        self._network_deferrals: dict[str, int] = {}
        self._gave_up: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def running_accounts(self) -> set[str]:
        return set(self._running)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # ---------------------------------------------------------------- 窗口换算

    @staticmethod
    def _parse_hhmm(value: str) -> time:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))

    def _window_for(self, account: Account) -> tuple[datetime, datetime]:
        """返回账号本地时区窗口起点/终点（今天）；跨午夜窗口时 end 取次日合并处理。"""
        tz = ZoneInfo(account.timezone)
        local_now = self._clock().astimezone(tz)
        start = self._parse_hhmm(account.send_window_start)
        end = self._parse_hhmm(account.send_window_end)
        start_dt = datetime.combine(local_now.date(), start, tzinfo=tz)
        end_dt = datetime.combine(local_now.date(), end, tzinfo=tz)
        if end < start:  # 跨午夜窗口（如 22:00-02:00）：end 移到次日
            end_dt = end_dt + timedelta(days=1)
        return start_dt, end_dt

    def _pick_scheduled_at(self, account: Account) -> datetime:
        start_dt, end_dt = self._window_for(account)
        if end_dt <= start_dt:
            return start_dt
        chosen = start_dt + timedelta(
            seconds=self._rng.uniform(0, (end_dt - start_dt).total_seconds())
        )
        return chosen.astimezone(ZoneInfo("UTC"))

    def _local_date(self, account: Account) -> date:
        return self._clock().astimezone(ZoneInfo(account.timezone)).date()

    @staticmethod
    def _utc_naive(dt: datetime | None) -> datetime | None:
        """SQLite 读出的 datetime 为 naive；统一为 UTC-naive 以便与 aware 时钟比较。"""
        if dt is None:
            return None
        if dt.tzinfo is not None:
            return dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        return dt

    # ---------------------------------------------------------------- 每日生成

    def generate_due_schedules(self, now: datetime | None = None) -> list[SendSchedule]:
        """为每个 enabled 账号幂等建当日计划（窗口内随机取时）。返回新建的计划。"""
        now = now or self._clock()
        created: list[SendSchedule] = []
        with Session(self._engine) as session:
            accounts = session.exec(
                select(Account).where(Account.enabled)
            ).all()
            for account in accounts:
                local_date = self._local_date(account)
                existing = session.exec(
                    select(SendSchedule).where(
                        SendSchedule.account_id == account.id,
                        SendSchedule.business_date == local_date,
                    )
                ).first()
                if existing is not None:
                    continue
                schedule = SendSchedule(
                    account_id=account.id,
                    business_date=local_date,
                    scheduled_at=self._pick_scheduled_at(account),
                    status=ScheduleStatus.PENDING,
                )
                session.add(schedule)
                created.append(schedule)
            try:
                session.commit()
            except Exception:  # 撞唯一约束：另一 worker 已建，忽略
                session.rollback()
                created = []
        return created

    # ---------------------------------------------------------------- 派发

    async def tick(self, now: datetime | None = None) -> dict:
        """一轮调度：恢复游离计划 → 生成当日 → 到点抢租约并触发发送。"""
        now = self._utc_naive(now or self._clock())
        if self._paused:
            return {"dispatched": 0}
        recovered = self._recover_expired(now)
        for account_id in recovered:
            await self._notify(
                "自动发送中断",
                f"账号「{self._account_name(account_id)}」的自动发送曾中断，结果未确认，已转待人工复核。",
                "timeSensitive",
            )
        self.generate_due_schedules(now)

        # 到点且可派发的计划（pending + failed 重试）
        with Session(self._engine) as session:
            due = session.exec(
                select(SendSchedule).where(
                    SendSchedule.status.in_(
                        [ScheduleStatus.PENDING, ScheduleStatus.FAILED]
                    )
                )
            ).all()

        dispatched = 0
        for schedule in due:
            if self._stopping:
                break
            if schedule.id in self._gave_up:
                continue  # 重试已耗尽，保持终态
            next_at = self._next_attempt_at.get(schedule.id)
            if next_at is not None and now < next_at:
                continue  # 退避窗口内，未到下次尝试时间
            scheduled_at = self._utc_naive(schedule.scheduled_at)
            if scheduled_at is None or scheduled_at > now:
                continue
            if not self._try_lease(schedule.id, now):
                continue  # 已被另一 worker 抢走或正在跑
            account = self._load_account(schedule.account_id)
            if account is None or not account.enabled:
                self._finalize(schedule.id, ScheduleStatus.SKIPPED)
                continue
            if not self._account_ready(account):
                # 账号状态不允许（未登录/失效/风控）：不派发，留待人工/后续轮次
                self._release(schedule.id)
                continue
            if self._window_expiry_policy == "skip" and self._window_expired(account):
                self._finalize(schedule.id, ScheduleStatus.SKIPPED, "窗口已结束，未派发")
                self._clear_retry_state(schedule.id)
                await self._notify(
                    "今日发送窗口已结束",
                    f"账号「{self._account_name(account.id)}」的今日计划未能在窗口内完成，已跳过；"
                    "如需补发请在控制台手动发送。",
                    "timeSensitive",
                )
                continue
            dispatched += 1
            self._spawn_send(schedule.id, schedule.account_id)
        return {"dispatched": dispatched}

    def _account_ready(self, account: Account) -> bool:
        # 只允许明确可发的健康账号；其余（含 auth_expired/verification/dom_broken）由人工接管
        return account.status == AccountStatus.HEALTHY

    def _window_expired(self, account: Account) -> bool:
        """当前账号本地时间是否已过发送窗口终点。"""
        _, end_dt = self._window_for(account)
        return self._clock().astimezone(ZoneInfo(account.timezone)) >= end_dt

    def _load_account(self, account_id: str) -> Account | None:
        with Session(self._engine) as session:
            return session.get(Account, account_id)

    # ---------------------------------------------------------------- 租约

    def _try_lease(self, schedule_id: str, now: datetime) -> bool:
        """条件更新抢占租约：仅当仍是 pending/failed（重试）且租约未过期时置为 leased。"""
        with Session(self._engine) as session:
            row = session.get(SendSchedule, schedule_id)
            if row is None:
                return False
            if row.status not in (ScheduleStatus.PENDING, ScheduleStatus.FAILED):
                return False
            lease_expires = self._utc_naive(row.lease_expires_at)
            if lease_expires is not None and lease_expires > now:
                return False  # 正被别的 worker 处理中
            row.status = ScheduleStatus.LEASED
            row.lease_owner = "scheduler"
            row.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            row.updated_at = now
            session.add(row)
            session.commit()
            return True

    def _release(self, schedule_id: str) -> None:
        with Session(self._engine) as session:
            row = session.get(SendSchedule, schedule_id)
            if row is not None and row.status in (
                ScheduleStatus.LEASED,
                ScheduleStatus.SENDING,
            ):
                row.status = ScheduleStatus.PENDING
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = utc_now()
                session.add(row)
                session.commit()

    def _recover_expired(self, now: datetime) -> set[str]:
        """崩溃恢复：租约过期且仍停在 leased/sending → 转 unknown_requires_review（不自动重发）。

        返回被恢复的账号 ID 集合（供调用方决定是否通知）。
        """
        recovered_accounts: set[str] = set()
        with Session(self._engine) as session:
            stale = session.exec(
                select(SendSchedule).where(
                    SendSchedule.status == ScheduleStatus.LEASED,
                )
            ).all()
            for row in stale:
                # 若对应账号仍在进程中发送，不做恢复
                if row.account_id in self._running:
                    continue
                lease_expires = self._utc_naive(row.lease_expires_at)
                if lease_expires is not None and lease_expires > now:
                    continue  # 租约未过期，仍在正常处理
                row.status = ScheduleStatus.UNKNOWN_REQUIRES_REVIEW
                row.last_error = "调度租约丢失，结果未确认"
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = now
                session.add(row)
                recovered_accounts.add(row.account_id)
            session.commit()
            # 仅把这些「确实崩溃」的账号下，尚在中间态的 job 一并转待核（防重复再发）
            if recovered_accounts:
                self._recover_stale_jobs(now, recovered_accounts)
        return recovered_accounts

    def _recover_stale_jobs(self, now: datetime, account_ids: set[str]) -> None:
        if not account_ids:
            return
        with Session(self._engine) as session:
            stale_jobs = session.exec(
                select(SendJob).where(
                    SendJob.account_id.in_(account_ids),
                    SendJob.status.in_(
                        [
                            JobStatus.LEASED,
                            JobStatus.OPENING_CHAT,
                            JobStatus.SENDING,
                            JobStatus.CONFIRMING,
                        ]
                    ),
                )
            ).all()
            for job in stale_jobs:
                job.status = JobStatus.UNKNOWN_REQUIRES_REVIEW
                job.updated_at = now
                session.add(job)
            session.commit()

    # ---------------------------------------------------------------- 发送

    def _spawn_send(self, schedule_id: str, account_id: str) -> None:
        self._running.add(account_id)
        task = asyncio.create_task(self._run_send(schedule_id, account_id))
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    async def _run_send(self, schedule_id: str, account_id: str) -> None:
        try:
            with Session(self._engine) as session:
                row = session.get(SendSchedule, schedule_id)
                if row is not None:
                    row.status = ScheduleStatus.SENDING
                    row.started_at = utc_now()
                    row.updated_at = utc_now()
                    session.add(row)
                    session.commit()

            if self._send_account is None:
                raise RuntimeError("未配置发送执行器")

            result = await asyncio.wait_for(
                self._send_account(account_id), timeout=self._run_timeout
            )
            await self._handle_result(schedule_id, account_id, result)
        except asyncio.CancelledError:
            # 进程内取消（如 stop()）：转 unknown_requires_review 待核
            self._finalize(schedule_id, ScheduleStatus.UNKNOWN_REQUIRES_REVIEW, "调度被取消")
            raise
        except Exception as exc:
            await self._handle_failure(
                schedule_id, account_id, overall="error", detail=type(exc).__name__
            )
        finally:
            self._running.discard(account_id)

    async def _handle_result(self, schedule_id: str, account_id: str, result: dict) -> None:
        overall = result.get("overall")
        if overall == "ok":
            self._finalize(schedule_id, ScheduleStatus.SUCCEEDED, None)
            self._clear_retry_state(schedule_id)
            self._reset_failure_streak(account_id)
            await self._notify_success(account_id, result)
            return
        if overall == "network_unavailable":
            await self._defer_network(schedule_id, account_id)
            return
        await self._handle_failure(
            schedule_id, account_id, overall=overall or "error", detail=overall
        )

    # ------------------------------------------------------------ M6-3 熔断/成功推送

    async def _notify_success(self, account_id: str, result: dict) -> None:
        """成功汇总推送：默认开启，让无人值守时「今天发完了」也有回音。"""
        if not self._notify_on_success:
            return
        results = result.get("results") or []
        total = len(results)
        ok = sum(
            1
            for item in results
            if item.get("status") in ("succeeded", "duplicate_succeeded")
        )
        await self._notify(
            "今日续焰完成",
            f"账号「{self._account_name(account_id)}」自动续火完成 {ok}/{total}。",
            "active",
        )

    def _reset_failure_streak(self, account_id: str) -> None:
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is not None and account.failure_streak:
                account.failure_streak = 0
                session.add(account)
                session.commit()

    async def _register_failure(self, account_id: str) -> None:
        """终态失败记账：累计连续失败；达到阈值转 cooldown 熔断并通知一次。"""
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                return
            account.failure_streak = (account.failure_streak or 0) + 1
            streak = account.failure_streak
            crossed = (
                self._failure_streak_limit > 0 and streak == self._failure_streak_limit
            )
            if crossed:
                account.status = AccountStatus.COOLDOWN
            account.updated_at = utc_now()
            session.add(account)
            session.commit()
        if crossed:
            await self._notify(
                "连续失败已熔断",
                f"账号「{self._account_name(account_id)}」已连续 "
                f"{streak} 天自动发送失败，已暂停其自动调度（cooldown）；"
                "请检查登录/页面结构后人工恢复。",
                "critical",
            )

    async def _handle_failure(
        self, schedule_id: str, account_id: str, *, overall: str, detail: str | None
    ) -> None:
        """partial/error/执行异常：窗口内有限次指数退避重试，耗尽才转终态并通知。

        重试复用 run_send 的幂等：已成功/unknown 的好友任务自动跳过，仅重试可重试项。
        auth_expired 需人工重新登录，重试无意义，直接终态。
        """
        if overall == "auth_expired":
            self._finalize(schedule_id, ScheduleStatus.FAILED, overall)
            self._clear_retry_state(schedule_id)
            await self._register_failure(account_id)
            await self._notify(
                "账号登录失效",
                f"账号「{self._account_name(account_id)}」登录已失效，请重新扫码登录以继续自动续火。",
                "critical",
            )
            return
        retries = self._retries.get(schedule_id, 0)
        if retries < self._retry_max_count:
            self._retries[schedule_id] = retries + 1
            backoff = self._retry_backoff_base * (2**retries)
            self._next_attempt_at[schedule_id] = self._utc_naive(
                self._clock()
            ) + timedelta(seconds=backoff)
            self._release(schedule_id)
            return
        self._gave_up.add(schedule_id)
        self._clear_retry_state(schedule_id)
        name = self._account_name(account_id)
        if overall == "partial":
            self._finalize(
                schedule_id,
                ScheduleStatus.UNKNOWN_REQUIRES_REVIEW,
                "部分好友结果未确认（已重试仍未全部确认）",
            )
            await self._register_failure(account_id)
            await self._notify(
                "部分好友结果未确认",
                f"账号「{name}」有好友发送结果未确认（已含重试），请到任务记录人工核对。",
                "timeSensitive",
            )
        else:
            self._finalize(schedule_id, ScheduleStatus.FAILED, detail or overall)
            await self._register_failure(account_id)
            await self._notify(
                "账号发送出错",
                f"账号「{name}」发送过程中出现错误（已重试仍未成功），需人工处理。",
                "errors",
            )

    async def _defer_network(self, schedule_id: str, account_id: str) -> None:
        """网络不可用：不消耗重试次数，延迟到下一轮再试；连续多次仍不可用则终态。"""
        count = self._network_deferrals.get(schedule_id, 0) + 1
        self._network_deferrals[schedule_id] = count
        if count > self._network_max_deferrals:
            self._gave_up.add(schedule_id)
            self._clear_retry_state(schedule_id)
            self._finalize(schedule_id, ScheduleStatus.FAILED, "网络持续不可用")
            await self._register_failure(account_id)
            await self._notify(
                "网络持续不可用",
                f"账号「{self._account_name(account_id)}」因网络不可用连续 "
                f"{self._network_max_deferrals + 1} 轮未能开始发送，今日计划已停止；"
                "网络恢复后请在控制台手动补发。",
                "errors",
            )
            return
        self._release(schedule_id)
        self._next_attempt_at[schedule_id] = self._utc_naive(
            self._clock()
        ) + timedelta(seconds=self._network_deferral_backoff)

    def _clear_retry_state(self, schedule_id: str) -> None:
        self._retries.pop(schedule_id, None)
        self._next_attempt_at.pop(schedule_id, None)
        self._network_deferrals.pop(schedule_id, None)

    def _account_name(self, account_id: str) -> str:
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            return account.display_name if account is not None else account_id

    async def _notify(self, title: str, content: str, level: str) -> None:
        try:
            await self._notifier.send(title, content, level)
        except Exception:  # noqa: BLE001 - 通知失败不影响调度主流程
            pass

    def _finalize(self, schedule_id: str, status: ScheduleStatus, error: str | None) -> None:
        with Session(self._engine) as session:
            row = session.get(SendSchedule, schedule_id)
            if row is None:
                return
            row.status = status
            row.finished_at = utc_now()
            row.updated_at = utc_now()
            row.last_error = error
            if status == ScheduleStatus.FAILED:
                row.attempt_count += 1
            row.lease_owner = None
            row.lease_expires_at = None
            session.add(row)
            session.commit()

    # ---------------------------------------------------------------- 生命周期

    async def wait_idle(self) -> None:
        """等待所有在途后台发送任务结束（测试/收尾用）。"""
        if self._send_tasks:
            await asyncio.gather(*tuple(self._send_tasks), return_exceptions=True)
            self._send_tasks.clear()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except Exception:
                # 调度循环自身绝不允许因单轮异常退出
                pass
            interval = self._loop_interval
            if interval <= 0:
                interval = 60.0
            await self._sleep(interval)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._send_tasks:
            for task in self._send_tasks:
                task.cancel()
            await asyncio.gather(*tuple(self._send_tasks), return_exceptions=True)
            self._send_tasks.clear()
