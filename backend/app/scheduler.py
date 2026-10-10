"""M4/M9 调度器：按账号发送窗口自动触发真实发送，并定期同步会话状态。

发送部分只负责「哪个账号今天何时发 + 账号级租约/互斥/恢复」，具体每个好友的发送仍走
BrowserManager.run_send（SendJob 幂等键兜底、终态确认已实机验证）。会话状态同步复用
后台扫描任务，只回写已在列表中的好友/群聊官方快照，不导入新对象、不发送消息。

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

from .core.config import (
    AUTO_SCAN_INTERVAL_MAX_HOURS,
    AUTO_SCAN_INTERVAL_MIN_HOURS,
    Settings,
)
from .models import (
    Account,
    AccountStatus,
    AppSetting,
    ContractTarget,
    ScheduleStatus,
    ScanTask,
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
# 会话状态同步默认每 24 小时一次；官方火花展示为天级状态，没必要高频访问会话列表。
DEFAULT_AUTO_SCAN_INTERVAL_HOURS = 24
# 服务启动后给手动登录/发送留出缓冲，再启动首次自动扫描。
DEFAULT_AUTO_SCAN_INITIAL_DELAY = 60.0
# 自动扫描避开发送窗口开始前的缓冲区，避免两个后台浏览器流程抢同一账号。
DEFAULT_AUTO_SCAN_GUARD_MINUTES = 15
# 与手动登录/发送/扫描发生账号级互斥时，短暂延后而不是错过一整天。
DEFAULT_AUTO_SCAN_CONFLICT_RETRY_MINUTES = 5
# 失败重试退避基数（秒），指数退避：60s, 120s, ...
RETRY_BACKOFF_BASE = 60.0
RETRY_MAX_COUNT = 2
# 网络不可用时每轮延迟重试的基数（秒）
NETWORK_DEFERRAL_BACKOFF = 60.0
# 网络不可用最多连续延迟次数，超过则终态失败并通知
NETWORK_MAX_DEFERRALS = 5
# 窗口过期策略：skip=窗口已结束不再派发（防深夜打扰）；dispatch=到点即发（旧行为）
WINDOW_EXPIRY_POLICY_DEFAULT = "skip"
# 到点判定容差（秒）。事件循环的定时器可能比目标时刻早若干毫秒返回，若固定时刻正好
# 落在整分边界上，「到点那一刻」会被判成「还没到点」，整轮推迟整整一分钟才发。
# 留 1 秒容差：整分到期的计划即使提前几毫秒醒来也照发，最坏只是早发不到 1 秒。
DUE_TOLERANCE_SECONDS = 1.0
# 节拍对齐的余量（秒）：醒来时刻取「边界 + 余量」，确保真的跨过边界。
TICK_ALIGN_MARGIN_SECONDS = 0.02
# 固定发送时刻的宽限期（分钟）：固定时刻即窗口终点，没有宽限期就会在到点那一刻
# 被判「已过期」，导致固定时刻账号在 skip 策略下永远发不出去。
FIXED_TIME_GRACE_MINUTES_DEFAULT = 30
FIXED_TIME_GRACE_MINUTES_MIN = 0
FIXED_TIME_GRACE_MINUTES_MAX = 24 * 60
# 全局自动调度暂停状态的持久化键；值为 "1" 表示暂停，"0" 表示运行。
SCHEDULER_PAUSED_SETTING_KEY = "scheduler.paused"
SCHEDULER_AUTO_SCAN_INTERVAL_SETTING_KEY = "scheduler.auto_scan_interval_hours"
SCHEDULER_ALWAYS_SEND_SETTING_KEY = "scheduler.scheduled_always_send"
SCHEDULER_FIXED_GRACE_SETTING_KEY = "scheduler.fixed_time_grace_minutes"

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
        send_targets: Callable[[str, list[str]], Awaitable[dict]] | None = None,
        scan_account: Callable[[str], Awaitable[dict]] | None = None,
        auto_scan_interval_hours: int = DEFAULT_AUTO_SCAN_INTERVAL_HOURS,
        auto_scan_initial_delay: float = DEFAULT_AUTO_SCAN_INITIAL_DELAY,
        auto_scan_guard_minutes: int = DEFAULT_AUTO_SCAN_GUARD_MINUTES,
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
        scheduled_always_send: bool = False,
        fixed_time_grace_minutes: int = FIXED_TIME_GRACE_MINUTES_DEFAULT,
        rng: random.Random | None = None,
        sleep=asyncio.sleep,
        clock: Callable[[], datetime] | None = None,
    ):
        self._engine = engine
        self._settings = settings
        self._send_account = send_account
        self._send_targets = send_targets
        self._scan_account = scan_account
        self._auto_scan_interval_hours = min(
            AUTO_SCAN_INTERVAL_MAX_HOURS,
            max(AUTO_SCAN_INTERVAL_MIN_HOURS, int(auto_scan_interval_hours)),
        )
        self._auto_scan_initial_delay = max(0.0, float(auto_scan_initial_delay))
        self._auto_scan_guard_minutes = max(0, int(auto_scan_guard_minutes))
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
        self._scheduled_always_send = scheduled_always_send
        self._fixed_time_grace_minutes = max(0, int(fixed_time_grace_minutes))
        self._rng = rng or random.Random()
        self._sleep = sleep
        self._clock = clock or utc_now
        self._due_tolerance = timedelta(seconds=DUE_TOLERANCE_SECONDS)
        self._running: set[str] = set()  # 当前正在发送的 account_id（进程内防并发的第二道闸）
        self._paused = False
        self._task: asyncio.Task | None = None
        self._send_tasks: set[asyncio.Task] = set()
        self._auto_scan_tasks: set[asyncio.Task] = set()
        self._auto_scan_running: set[str] = set()
        self._auto_scan_next_attempt_at: dict[str, datetime] = {}
        self._auto_scan_not_before: datetime | None = None
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

    @property
    def auto_scan_interval_hours(self) -> int:
        return self._auto_scan_interval_hours

    @property
    def auto_scan_running_accounts(self) -> set[str]:
        return set(self._auto_scan_running)

    @property
    def scheduled_always_send(self) -> bool:
        return self._scheduled_always_send

    @property
    def fixed_time_grace_minutes(self) -> int:
        return self._fixed_time_grace_minutes

    def load_persisted_state(self) -> None:
        """从数据库恢复全局调度开关、会话同步周期与「到点重发」开关。"""
        with Session(self._engine) as session:
            paused_setting = session.get(AppSetting, SCHEDULER_PAUSED_SETTING_KEY)
            self._paused = paused_setting is not None and paused_setting.value == "1"
            interval_setting = session.get(
                AppSetting,
                SCHEDULER_AUTO_SCAN_INTERVAL_SETTING_KEY,
            )
            if interval_setting is not None:
                try:
                    persisted_interval = int(interval_setting.value)
                except (TypeError, ValueError):
                    persisted_interval = None
                if (
                    persisted_interval is not None
                    and AUTO_SCAN_INTERVAL_MIN_HOURS
                    <= persisted_interval
                    <= AUTO_SCAN_INTERVAL_MAX_HOURS
                ):
                    self._auto_scan_interval_hours = persisted_interval
            always_send_setting = session.get(
                AppSetting,
                SCHEDULER_ALWAYS_SEND_SETTING_KEY,
            )
            if always_send_setting is not None:
                self._scheduled_always_send = always_send_setting.value == "1"
            grace_setting = session.get(
                AppSetting,
                SCHEDULER_FIXED_GRACE_SETTING_KEY,
            )
            if grace_setting is not None:
                try:
                    persisted_grace = int(grace_setting.value)
                except (TypeError, ValueError):
                    persisted_grace = None
                if persisted_grace is not None and (
                    FIXED_TIME_GRACE_MINUTES_MIN
                    <= persisted_grace
                    <= FIXED_TIME_GRACE_MINUTES_MAX
                ):
                    self._fixed_time_grace_minutes = persisted_grace

    def _persist_paused(self, paused: bool) -> None:
        with Session(self._engine) as session:
            setting = session.get(AppSetting, SCHEDULER_PAUSED_SETTING_KEY)
            if setting is None:
                setting = AppSetting(
                    key=SCHEDULER_PAUSED_SETTING_KEY,
                    value="1" if paused else "0",
                )
            else:
                setting.value = "1" if paused else "0"
                setting.updated_at = utc_now()
            session.add(setting)
            session.commit()

    def _persist_auto_scan_interval(self, hours: int) -> None:
        with Session(self._engine) as session:
            setting = session.get(AppSetting, SCHEDULER_AUTO_SCAN_INTERVAL_SETTING_KEY)
            if setting is None:
                setting = AppSetting(
                    key=SCHEDULER_AUTO_SCAN_INTERVAL_SETTING_KEY,
                    value=str(hours),
                )
            else:
                setting.value = str(hours)
                setting.updated_at = utc_now()
            session.add(setting)
            session.commit()

    def update_auto_scan_interval(self, hours: int) -> None:
        """持久化自动同步周期；下一次调度按新周期重新计算。"""
        if isinstance(hours, bool) or not (
            AUTO_SCAN_INTERVAL_MIN_HOURS <= hours <= AUTO_SCAN_INTERVAL_MAX_HOURS
        ):
            raise ValueError(
                f"自动同步周期必须在 {AUTO_SCAN_INTERVAL_MIN_HOURS} 到 "
                f"{AUTO_SCAN_INTERVAL_MAX_HOURS} 小时之间"
            )
        # 先写库再切换内存值，避免持久化失败时运行态与重启后的配置不一致。
        self._persist_auto_scan_interval(hours)
        self._auto_scan_interval_hours = hours
        # 互斥冲突的短暂退避属于旧周期，设置变更后让数据库扫描时间重新决定下一次触发。
        self._auto_scan_next_attempt_at.clear()

    def _persist_scheduled_always_send(self, enabled: bool) -> None:
        with Session(self._engine) as session:
            setting = session.get(AppSetting, SCHEDULER_ALWAYS_SEND_SETTING_KEY)
            if setting is None:
                setting = AppSetting(
                    key=SCHEDULER_ALWAYS_SEND_SETTING_KEY,
                    value="1" if enabled else "0",
                )
            else:
                setting.value = "1" if enabled else "0"
                setting.updated_at = utc_now()
            session.add(setting)
            session.commit()

    def update_scheduled_always_send(self, enabled: bool) -> None:
        """持久化「到点重发」开关；下一次 tick 即按新值派发。"""
        # 先写库再切换内存值，避免持久化失败时运行态与重启后的配置不一致。
        self._persist_scheduled_always_send(enabled)
        self._scheduled_always_send = bool(enabled)

    def update_fixed_time_grace_minutes(self, minutes: int) -> None:
        """持久化固定时刻宽限期（分钟）；下一次 tick 即按新值判断「是否已错过」。"""
        if isinstance(minutes, bool) or not (
            FIXED_TIME_GRACE_MINUTES_MIN <= minutes <= FIXED_TIME_GRACE_MINUTES_MAX
        ):
            raise ValueError(
                f"固定时刻宽限期必须在 {FIXED_TIME_GRACE_MINUTES_MIN} 到 "
                f"{FIXED_TIME_GRACE_MINUTES_MAX} 分钟之间"
            )
        with Session(self._engine) as session:
            setting = session.get(AppSetting, SCHEDULER_FIXED_GRACE_SETTING_KEY)
            if setting is None:
                setting = AppSetting(
                    key=SCHEDULER_FIXED_GRACE_SETTING_KEY,
                    value=str(minutes),
                )
            else:
                setting.value = str(minutes)
                setting.updated_at = utc_now()
            session.add(setting)
            session.commit()
        self._fixed_time_grace_minutes = int(minutes)

    def pause(self) -> None:
        self._paused = True
        # 暂停失败时保持内存中的“暂停”状态，避免故障时继续自动发送。
        self._persist_paused(True)

    def resume(self) -> None:
        # 先写库，只有持久化成功后才恢复发送；失败时保持暂停以确保安全。
        self._persist_paused(False)
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
        # 账号设置了精确发送时刻：不再窗口随机，固定在该时刻（按账号时区换算到 UTC）
        tz = ZoneInfo(account.timezone)
        if account.send_time_hhmm:
            chosen = datetime.combine(
                self._clock().astimezone(tz).date(),
                self._parse_hhmm(account.send_time_hhmm),
                tzinfo=tz,
            )
            return chosen.astimezone(ZoneInfo("UTC"))
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
        """一轮调度：恢复游离计划 → 生成当日 → 好友级准点先发 → 账号批量 → 自动扫描。"""
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

        # 好友级精确时刻先派发，并等它跑完再发账号批量：设了独立时刻的好友（例如「要准点
        # 最先发」的那一个）在整点第一个真正发出，其余好友紧接着在同一分钟内开跑；否则
        # 账号批量会先抢走同一个浏览器，把准点好友挤到下一轮（60 秒后）。
        pending_targets = await self._dispatch_due_targets(now)
        if pending_targets:
            await asyncio.gather(*pending_targets, return_exceptions=True)

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
            if scheduled_at is None or scheduled_at - now > self._due_tolerance:
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
        # 自动扫描最后检查账号租约，避免与发送同时抢同一浏览器。
        self._dispatch_auto_scans(now)
        return {"dispatched": dispatched}

    def _account_ready(self, account: Account) -> bool:
        # 只允许明确可发的健康账号；其余（含 auth_expired/verification/dom_broken）由人工接管
        return account.status == AccountStatus.HEALTHY

    def _window_expired(self, account: Account) -> bool:
        """当前账号本地时间是否已过发送窗口终点。

        账号设置固定发送时刻时，该时刻本身即为「窗口终点」：过了宽限期仍未派发，
        今天这个固定槽位即视为错过，不深夜补发（与窗口随机语义一致的 skip 策略）。

        宽限期是必需的：`>=` 会让计划在到点那一刻就被判「窗口已结束」，于是固定时刻
        账号在 skip 策略下永远发不出去（发送器从不被调用）。宽限期内仍按到点处理，
        同时保留「错过就不补发」的原意——不会因为机器凌晨没开就在白天补发。
        """
        tz = ZoneInfo(account.timezone)
        local_now = self._clock().astimezone(tz)
        if account.send_time_hhmm:
            fixed = datetime.combine(
                local_now.date(), self._parse_hhmm(account.send_time_hhmm), tzinfo=tz
            )
            return local_now >= fixed + timedelta(minutes=self._fixed_time_grace_minutes)
        _, end_dt = self._window_for(account)
        return local_now >= end_dt

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

    def _target_time_aware(self, account: Account, hhmm: str, now: datetime) -> datetime:
        """把好友本地时间 HH:MM（账号时区）换算到 UTC-naive 时刻，跨午夜取最近的一天。"""
        tz = ZoneInfo(account.timezone)
        aware_now = (
            now.replace(tzinfo=ZoneInfo("UTC")) if now.tzinfo is None else now
        ).astimezone(tz)
        target = datetime.combine(aware_now.date(), self._parse_hhmm(hhmm), tzinfo=tz)
        # 若该时刻在今天稍后（尚未到），则仍是「今天」的计划；已过去则视为已过
        return target.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    async def _dispatch_due_targets(self, now: datetime) -> list[asyncio.Task]:
        """好友级精确发送时刻到点派发：各自到点单独发，遵守同账号并发=1与每日幂等。

        只处理启用 + 非软删 + 设置独立 send_time_hhmm 的好友，且账号 healthy。
        到点后走 send_targets 执行器（内部复用 run_send 的 SendJob 幂等，绝不重复发送）。

        返回已派发的任务，供 tick 等在同一个节拍内跑完再派发账号批量。
        """
        spawned: list[asyncio.Task] = []
        if self._send_targets is None:
            return spawned
        with Session(self._engine) as session:
            rows = session.exec(
                select(ContractTarget, Account)
                .join(Account, ContractTarget.account_id == Account.id)
                .where(
                    ContractTarget.enabled,
                    ContractTarget.deleted_at.is_(None),
                    ContractTarget.send_time_hhmm.is_not(None),
                )
            ).all()

        for target, account in rows:
            if self._stopping:
                break
            if account.id in self._running:
                continue  # 同账号并发=1：正有发送/扫描占着浏览器
            if not account.enabled or account.status != AccountStatus.HEALTHY:
                continue
            due_at = self._target_time_aware(account, target.send_time_hhmm, now)
            if due_at is None or due_at - now > self._due_tolerance:
                continue
            resend_slot = (
                f"sched{self._clock().astimezone(ZoneInfo(account.timezone)).date():%Y%m%d}"
                if self._scheduled_always_send
                else None
            )
            # 幂等：本轮槽位已有发送记录的不重发（与账号批量逻辑一致）
            if self._target_done_today(account=account, target=target, resend_slot=resend_slot):
                continue
            self._running.add(account.id)
            task = asyncio.create_task(
                self._run_target_send(account.id, [target.id], force_run_id=resend_slot)
            )
            self._send_tasks.add(task)
            task.add_done_callback(self._send_tasks.discard)
            spawned.append(task)
        return spawned

    def _target_done_today(self, *, account, target, resend_slot: str | None = None) -> bool:
        """该好友今天是否已按本轮槽位发送过——发过则不重复派发。

        默认任何当日发送记录都算已发（每天一次）。开启 scheduled_always_send 时只认
        「按业务日固定」的槽位：当天手动或账号批量发过也照样派发，即用户要的
        「到点就发一遍，不管有没有发过」；重试复用同一槽位，因此不会重复发送。
        """
        with Session(self._engine) as session:
            business_date = self._clock().astimezone(ZoneInfo(account.timezone)).date()
            statement = select(SendJob).where(
                SendJob.account_id == account.id,
                SendJob.target_id == target.id,
                SendJob.business_date == business_date,
            )
            if resend_slot is not None:
                statement = statement.where(
                    SendJob.message_slot_id.like(f"%:force:{resend_slot}")
                )
            job = session.exec(statement).first()
            return job is not None

    async def _run_target_send(
        self, account_id: str, target_ids: list[str], *, force_run_id: str | None = None
    ) -> None:
        try:
            if self._send_targets is None:
                raise RuntimeError("未配置单好友发送执行器")
            await asyncio.wait_for(
                self._call_send_targets(
                    account_id, target_ids, force_run_id=force_run_id
                ),
                timeout=self._run_timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # 单好友精确派发失败：不自动重发，交由人工/下一轮（幂等键兜底）
            await self._notify(
                "自动发送中断",
                f"账号「{self._account_name(account_id)}」的好友级定时发送未成功，请人工复核。",
                "timeSensitive",
            )
        finally:
            self._running.discard(account_id)

    def _call_send_targets(
        self, account_id: str, target_ids: list[str], *, force_run_id: str | None = None
    ) -> Awaitable[dict]:
        """调用单好友发送执行器，兼容旧签名（send_targets(account_id, ids)）。"""
        if self._send_targets is None:
            raise RuntimeError("未配置单好友发送执行器")
        kwargs: dict = {}
        if force_run_id is not None:
            kwargs["force_run_id"] = force_run_id
        try:
            result = self._send_targets(account_id, target_ids, **kwargs)
        except TypeError:
            result = self._send_targets(account_id, target_ids)
        if asyncio.iscoroutine(result):
            return result
        return asyncio.ensure_future(result)  # type: ignore[return-value]

    def _call_send_account(
        self, account_id: str, *, exclude_scheduled: bool, force_run_id: str | None = None
    ) -> Awaitable[dict]:
        """调用发送执行器，兼容旧签名（send_account(account_id)）与新签名（支持 exclude_scheduled）。"""
        if self._send_account is None:
            raise RuntimeError("未配置发送执行器")
        kwargs: dict = {"exclude_scheduled": exclude_scheduled}
        if force_run_id is not None:
            kwargs["force_run_id"] = force_run_id
        try:
            # 新签名：browser_manager.run_send(account_id, exclude_scheduled=True)
            result = self._send_account(account_id, **kwargs)
        except TypeError:
            # 旧签名：send_account(account_id)（测试替身等），忽略该参数
            result = self._send_account(account_id)
        if asyncio.iscoroutine(result):
            return result
        return asyncio.ensure_future(result)  # type: ignore[return-value]

    def _spawn_send(self, schedule_id: str, account_id: str) -> None:
        self._running.add(account_id)
        task = asyncio.create_task(self._run_send(schedule_id, account_id))
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    async def _run_send(self, schedule_id: str, account_id: str) -> None:
        try:
            business_date: date | None = None
            with Session(self._engine) as session:
                row = session.get(SendSchedule, schedule_id)
                if row is not None:
                    row.status = ScheduleStatus.SENDING
                    row.started_at = utc_now()
                    row.updated_at = utc_now()
                    business_date = row.business_date
                    session.add(row)
                    session.commit()

            if self._send_account is None:
                raise RuntimeError("未配置发送执行器")

            # 开启 scheduled_always_send 时用「按业务日固定」的独立槽位发送：同一天
            # 手动/自动发过也照发，而重试复用同一槽位，已成功的好友不会因重试重复发送。
            resend_slot = (
                f"sched{business_date:%Y%m%d}"
                if self._scheduled_always_send and business_date is not None
                else None
            )
            # 自动账号级批量只发「未设置独立精确时刻」的好友；独立时刻好友由 _dispatch_due_targets 单独派发。
            result = await asyncio.wait_for(
                self._call_send_account(
                    account_id, exclude_scheduled=True, force_run_id=resend_slot
                ),
                timeout=self._run_timeout,
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

    # ------------------------------------------------------------ 会话状态自动同步

    def _latest_scan(self, account_id: str) -> ScanTask | None:
        with Session(self._engine) as session:
            return session.exec(
                select(ScanTask)
                .where(ScanTask.account_id == account_id)
                .order_by(ScanTask.updated_at.desc())
                .limit(1)
            ).first()

    def _has_targets(self, account_id: str) -> bool:
        with Session(self._engine) as session:
            return (
                session.exec(
                    select(ContractTarget.id)
                    .where(
                        ContractTarget.account_id == account_id,
                        ContractTarget.deleted_at.is_(None),
                    )
                    .limit(1)
                ).first()
                is not None
            )

    def _auto_scan_blocked_by_send_window(self, account: Account, now: datetime) -> bool:
        """自动扫描避开发送窗口及其前置缓冲，避免占用同账号浏览器。"""
        tz = ZoneInfo(account.timezone)
        aware_now = (
            now.replace(tzinfo=ZoneInfo("UTC")) if now.tzinfo is None else now
        ).astimezone(tz)
        start = self._parse_hhmm(account.send_window_start)
        end = self._parse_hhmm(account.send_window_end)
        start_dt = datetime.combine(aware_now.date(), start, tzinfo=tz)
        end_dt = datetime.combine(aware_now.date(), end, tzinfo=tz)
        if end < start:
            if aware_now < end_dt:
                start_dt -= timedelta(days=1)
            else:
                end_dt += timedelta(days=1)
        guard_start = start_dt - timedelta(minutes=self._auto_scan_guard_minutes)
        return guard_start <= aware_now < end_dt

    def _auto_scan_due(self, account: Account, now: datetime) -> bool:
        if self._scan_account is None:
            return False
        if self._auto_scan_not_before is not None:
            not_before = self._utc_naive(self._auto_scan_not_before)
            if not_before is not None and now < not_before:
                return False
        if account.id in self._auto_scan_running or account.id in self._running:
            return False
        latest = self._latest_scan(account.id)
        if latest is not None and latest.status == "running":
            return False
        # 登录刚恢复后，立即补做一次此前因失效而跳过的状态同步。
        if (
            latest is not None
            and latest.overall == "auth_expired"
            and account.status == AccountStatus.HEALTHY
        ):
            return True
        retry_at = self._auto_scan_next_attempt_at.get(account.id)
        if retry_at is not None and now < retry_at:
            return False
        if latest is None:
            return True
        reference = latest.updated_at or latest.finished_at or latest.started_at
        reference = self._utc_naive(reference)
        if reference is None:
            return True
        return now >= reference + timedelta(hours=self._auto_scan_interval_hours)

    def _dispatch_auto_scans(self, now: datetime) -> int:
        if self._scan_account is None or self._stopping:
            return 0
        with Session(self._engine) as session:
            accounts = session.exec(select(Account).where(Account.enabled)).all()

        dispatched = 0
        for account in accounts:
            if account.status != AccountStatus.HEALTHY:
                continue
            if not self._has_targets(account.id):
                continue
            if self._auto_scan_blocked_by_send_window(account, now):
                continue
            if not self._auto_scan_due(account, now):
                continue
            # 即使回调因外部互斥提前失败，也不要每 60 秒反复打开浏览器；
            # 正常回调会创建 ScanTask，之后由数据库时间窗控制下一次扫描。
            self._auto_scan_next_attempt_at[account.id] = now + timedelta(
                hours=self._auto_scan_interval_hours
            )
            self._spawn_auto_scan(account.id)
            dispatched += 1
        return dispatched

    def _spawn_auto_scan(self, account_id: str) -> None:
        self._auto_scan_running.add(account_id)
        task = asyncio.create_task(self._run_auto_scan(account_id))
        self._auto_scan_tasks.add(task)
        task.add_done_callback(
            lambda completed: self._finish_auto_scan_task(account_id, completed)
        )

    def _finish_auto_scan_task(self, account_id: str, task: asyncio.Task) -> None:
        self._auto_scan_tasks.discard(task)
        self._auto_scan_running.discard(account_id)

    async def _run_auto_scan(self, account_id: str) -> None:
        if self._scan_account is None:
            return
        try:
            result = await self._scan_account(account_id)
            overall = result.get("overall")
            if overall == "auth_expired":
                await self._notify(
                    "会话状态同步失败",
                    f"账号「{self._account_name(account_id)}」登录已失效，官方火花状态暂未更新；"
                    "重新扫码后会自动补做同步。",
                    "critical",
                )
        except asyncio.CancelledError:
            # 扫描子任务由 BrowserManager.aclose 收束为 interrupted，避免误记成用户取消。
            raise
        except Exception as exc:
            if getattr(exc, "status_code", None) == 409:
                # 账号正被手动流程占用；这不是扫描失败，下一轮短暂退避后再尝试。
                retry_at = self._utc_naive(self._clock()) or datetime.now()
                self._auto_scan_next_attempt_at[account_id] = retry_at + timedelta(
                    minutes=DEFAULT_AUTO_SCAN_CONFLICT_RETRY_MINUTES
                )
                return
            # 失败详情已经落在 ScanTask；调度循环不能因单账号同步异常退出。
            return

    def auto_scan_status(self) -> list[dict]:
        """返回每个账号的自动同步状态，供总览说明「何时会更新」。"""
        now = self._utc_naive(self._clock()) or datetime.now()
        with Session(self._engine) as session:
            accounts = session.exec(select(Account).order_by(Account.created_at)).all()

        statuses: list[dict] = []
        interval = timedelta(hours=self._auto_scan_interval_hours)
        for account in accounts:
            latest = self._latest_scan(account.id)
            has_targets = self._has_targets(account.id)
            last_at = None
            if latest is not None:
                last_at = latest.finished_at or latest.started_at
            status = "scheduled"
            detail = None
            next_at = None
            if not account.enabled:
                status = "disabled"
                detail = "账号已停用"
            elif not has_targets:
                status = "no_targets"
                detail = "暂无契约对象"
            elif account.status != AccountStatus.HEALTHY:
                status = "waiting_login"
                detail = "账号未处于可用登录状态"
            elif account.id in self._auto_scan_running:
                status = "running"
                detail = "正在读取会话列表"
            elif self._paused:
                status = "paused"
                detail = "全局自动调度已暂停"
            else:
                retry_at = self._auto_scan_next_attempt_at.get(account.id)
                if retry_at is not None and retry_at > now:
                    next_at = retry_at
                elif latest is not None and latest.overall == "auth_expired":
                    next_at = now
                elif last_at is not None:
                    next_at = self._utc_naive(last_at) + interval
                else:
                    next_at = self._auto_scan_not_before or now
                if next_at <= now:
                    status = "due"
                    detail = "等待调度器启动同步"
                else:
                    status = "scheduled"
            statuses.append(
                {
                    "account_id": account.id,
                    "task_id": latest.id if status == "running" and latest is not None else None,
                    "status": status,
                    "last_scan_at": last_at,
                    "next_scan_at": next_at,
                    "detail": detail,
                }
            )
        return statuses

    # ---------------------------------------------------------------- 生命周期

    async def wait_idle(self) -> None:
        """等待所有在途后台发送与自动扫描任务结束（测试/收尾用）。"""
        tasks = tuple(self._send_tasks) + tuple(self._auto_scan_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._send_tasks.clear()
        self._auto_scan_tasks.clear()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except Exception:
                # 调度循环自身绝不允许因单轮异常退出
                pass
            await self._sleep(self._seconds_until_next_tick())

    def _seconds_until_next_tick(self) -> float:
        """距下一个节拍边界的秒数（醒来落在「边界 + 余量」）。

        默认 60 秒节拍对齐到整分边界。否则「固定 02:22」会被进程启动相位带着漂移
        （例如容器 13:17:25 启动 → 每轮 tick 都在 :25 触发），可能整分错过了用户
        要求的准点窗口；对齐后固定时刻在 :00 触发。

        余量是必需的：事件循环的定时器可能比目标时刻早几毫秒返回，正好落在整分边界
        之前时，「到点那一刻」会被判成「还没到点」，把整轮推迟到下一分钟。
        """
        interval = self._loop_interval
        if interval <= 0:
            interval = 60.0
        remainder = self._clock().timestamp() % interval
        return max(interval - remainder, 0.0) + TICK_ALIGN_MARGIN_SECONDS

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.load_persisted_state()
        self._stopping = False
        self._auto_scan_not_before = self._utc_naive(self._clock()) + timedelta(
            seconds=self._auto_scan_initial_delay
        )
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
        if self._auto_scan_tasks:
            for task in self._auto_scan_tasks:
                task.cancel()
            await asyncio.gather(*tuple(self._auto_scan_tasks), return_exceptions=True)
            self._auto_scan_tasks.clear()
        self._auto_scan_running.clear()
