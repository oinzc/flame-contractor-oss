"""M4：调度器——当日计划生成、窗口随机取时、到期触发、账号互斥、崩溃恢复、暂停、端到端。"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.models import (
    Account,
    AccountStatus,
    AppSetting,
    ContractTarget,
    JobStatus,
    ScanTask,
    ScanTaskStatus,
    ScheduleStatus,
    SendJob,
    SendSchedule,
)
from app.scheduler import Scheduler
from tests.fakes import FakePage, create_account, make_app


def make_clock(start: datetime):
    now = {"value": start}

    def clock() -> datetime:
        return now["value"]

    clock.set = lambda value: now.__setitem__("value", value)  # type: ignore[attr-defined]
    return clock


def make_account(engine, *, status=AccountStatus.HEALTHY, enabled=True, **kwargs) -> Account:
    with Session(engine) as session:
        account = Account(
            display_name=kwargs.pop("display_name", "调度账号"),
            status=status,
            enabled=enabled,
            **kwargs,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        return account


def schedule_rows(engine) -> list[SendSchedule]:
    with Session(engine) as session:
        return session.exec(select(SendSchedule)).all()


def test_generate_uses_account_exact_time_if_set(engine):
    """账号设置 send_time_hhmm 后，当日计划固定在该时刻（按账号时区换 UTC），不再窗口随机。"""
    make_account(engine, send_time_hhmm="21:30")
    clock = make_clock(datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc))
    sched = Scheduler(engine, settings=None, clock=clock, sleep=asyncio.sleep, loop_interval=0)
    sched.generate_due_schedules()
    rows = schedule_rows(engine)
    assert len(rows) == 1
    # 21:30 Asia/Shanghai = 13:30 UTC（SQLite 读回为 naive UTC）
    assert rows[0].scheduled_at == datetime(2026, 8, 27, 13, 30)


def test_dispatch_due_targets_sends_only_exact_scheduled_target(engine):
    """好友级精确时刻到点（且账号 healthy）时，单独派发该好友；未设时间好友不被派发。"""
    account = make_account(engine)
    with Session(engine) as session:
        session.add(ContractTarget(account_id=account.id, nickname_snapshot="定时A", send_time_hhmm="09:00"))
        session.add(ContractTarget(account_id=account.id, nickname_snapshot="普通B"))
        session.commit()

    # 时钟设在 09:00 Asia/Shanghai = 01:00 UTC，让「定时A」到点
    clock = make_clock(datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc))
    calls: list[tuple[str, list[str]]] = []

    async def send_targets(account_id: str, target_ids: list[str]) -> dict:
        calls.append((account_id, target_ids))
        return {"overall": "ok"}

    sched = Scheduler(
        engine,
        settings=None,
        send_targets=send_targets,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    # 只有设置了精确时刻且到点的「定时A」被派发；「普通B」未被派发
    assert len(calls) == 1
    with Session(engine) as session:
        ta = session.exec(
            select(ContractTarget).where(ContractTarget.nickname_snapshot == "定时A")
        ).first()
        tb = session.exec(
            select(ContractTarget).where(ContractTarget.nickname_snapshot == "普通B")
        ).first()
    assert calls[0][0] == account.id
    assert calls[0][1] == [ta.id]
    assert tb.id not in calls[0][1]


def test_dispatch_due_targets_not_before_scheduled(engine):
    """好友精确时刻未到（now < scheduled）不派发。"""
    account = make_account(engine)
    with Session(engine) as session:
        session.add(ContractTarget(account_id=account.id, nickname_snapshot="定时A", send_time_hhmm="22:00"))
        session.commit()

    # 22:00 Asia/Shanghai = 14:00 UTC；现在 13:00 UTC 尚早
    clock = make_clock(datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc))
    calls: list[tuple[str, list[str]]] = []

    async def send_targets(account_id: str, target_ids: list[str]) -> dict:
        calls.append((account_id, target_ids))
        return {"overall": "ok"}

    sched = Scheduler(
        engine,
        settings=None,
        send_targets=send_targets,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    assert calls == []


# ---------------------------------------------------------------- 窗口与随机取时


def test_generate_creates_one_schedule_per_enabled_account(engine):
    account = make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc))
    sched = Scheduler(engine, settings=None, clock=clock, sleep=asyncio.sleep, rng=__import__("random").Random(1), loop_interval=0)  # type: ignore[arg-type]

    created = sched.generate_due_schedules()
    assert len(created) == 1
    rows = schedule_rows(engine)
    assert rows[0].account_id == account.id
    assert rows[0].business_date.year == 2026
    assert rows[0].status == ScheduleStatus.PENDING
    # scheduled_at 落在窗口内（20:00-22:00 Asia/Shanghai = 12:00-14:00 UTC）
    assert datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc) <= rows[0].scheduled_at.replace(
        tzinfo=timezone.utc
    ) <= datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)


def test_generate_is_idempotent_per_date(engine):
    make_account(engine, display_name="A")
    clock = make_clock(datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc))
    sched = Scheduler(engine, settings=None, clock=clock, sleep=asyncio.sleep, loop_interval=0)
    sched.generate_due_schedules()
    second = sched.generate_due_schedules()
    assert second == []
    assert len(schedule_rows(engine)) == 1  # 同账号同业务日期唯一


def test_generate_skips_disabled_account(engine):
    make_account(engine, enabled=False)
    clock = make_clock(datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc))
    sched = Scheduler(engine, settings=None, clock=clock, sleep=asyncio.sleep, loop_interval=0)
    assert sched.generate_due_schedules() == []
    assert schedule_rows(engine) == []


# ---------------------------------------------------------------- 到期派发与互斥


def test_tick_dispatches_only_when_due(engine):
    import random

    api = make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc))
    calls: list[str] = []

    async def send(account_id: str) -> dict:
        calls.append(account_id)
        return {"overall": "ok"}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        clock=clock,
        sleep=asyncio.sleep,
        rng=random.Random(1),
        loop_interval=0,
        # 该测试验证「到期即派发」语义，显式关闭窗口过期跳过策略
        window_expiry_policy="dispatch",
    )
    # 生成计划（scheduled_at 在窗口内随机 = 12:00-14:00 UTC）
    sched.generate_due_schedules()
    # now=09:00 早于窗口起点 → 必然不派发
    asyncio.run(sched.tick())
    assert calls == []

    # now 推到窗口之后（15:00）→ 派发并成功
    clock.set(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    asyncio.run(sched.tick())
    assert calls == [api.id]
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.SUCCEEDED


def test_account_not_healthy_not_dispatched(engine):
    api = make_account(engine, status=AccountStatus.AUTH_EXPIRED)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    calls: list[str] = []

    async def send(account_id: str) -> dict:
        calls.append(account_id)
        return {"overall": "ok"}

    sched = Scheduler(engine, settings=None, send_account=send, clock=clock, sleep=asyncio.sleep, loop_interval=0)
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    assert calls == []  # 账号失效，不自动发
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.PENDING  # 释放回 pending，可人工接管


def test_partial_result_becomes_unknown(engine):
    make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))

    async def send(account_id: str) -> dict:
        return {"overall": "partial"}

    sched = Scheduler(engine, settings=None, send_account=send, clock=clock, sleep=asyncio.sleep, loop_interval=0, retry_max_count=0, window_expiry_policy="dispatch")
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.UNKNOWN_REQUIRES_REVIEW


# ---------------------------------------------------------------- 崩溃恢复


def test_expired_lease_recovered_to_unknown(engine):
    api = make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    sched = Scheduler(engine, settings=None, clock=clock, sleep=asyncio.sleep, loop_interval=0)
    # 手工造一个「租约过期且仍 leased」的计划（模拟 worker 崩溃）
    with Session(engine) as session:
        row = SendSchedule(
            account_id=api.id,
            business_date=datetime(2026, 8, 27, 0, 0).date(),
            scheduled_at=datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc),
            status=ScheduleStatus.LEASED,
            lease_owner="dead-worker",
            lease_expires_at=datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc),
        )
        session.add(row)
        session.commit()

    asyncio.run(sched.tick())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.UNKNOWN_REQUIRES_REVIEW


def test_crash_recovery_marks_stale_jobs_unknown(settings):
    """账号调度崩溃：其下卡在中间态的 job 一并转 unknown_requires_review。"""
    from fastapi.testclient import TestClient

    app = make_app(settings, [])
    with TestClient(app) as client:
        account = create_account(client)
        tmpl = client.post(
            "/api/templates", json={"name": "T", "content": "{{target_name}}"}
        ).json()
        target = client.post(
            "/api/targets",
            json={
                "accountId": account["id"],
                "nicknameSnapshot": "好友A",
                "templateId": tmpl["id"],
            },
        ).json()

        clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
        sched = Scheduler(
            app.state.engine, settings=None, clock=clock, sleep=asyncio.sleep, loop_interval=0
        )
        # 手工造「租约过期且仍 leased」的计划 + 其下卡在 SENDING 的 job
        with Session(app.state.engine) as session:
            row = SendSchedule(
                account_id=account["id"],
                business_date=datetime(2026, 8, 27, 0, 0).date(),
                status=ScheduleStatus.LEASED,
                lease_owner="dead-worker",
                lease_expires_at=datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc),
            )
            session.add(row)
            job = SendJob(
                account_id=account["id"],
                target_id=target["id"],
                template_id=tmpl["id"],
                business_date=datetime(2026, 8, 27, 0, 0).date(),
                message_slot_id="T:v1",
                status=JobStatus.SENDING,
            )
            session.add(job)
            session.commit()

        asyncio.run(sched.tick())
        with Session(app.state.engine) as session:
            refreshed = session.exec(select(SendJob)).one()
            assert refreshed.status == JobStatus.UNKNOWN_REQUIRES_REVIEW


# ---------------------------------------------------------------- 暂停


def test_paused_stops_dispatch(engine):
    make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    calls: list[str] = []

    async def send(account_id: str) -> dict:
        calls.append(account_id)
        return {"overall": "ok"}

    sched = Scheduler(engine, settings=None, send_account=send, clock=clock, sleep=asyncio.sleep, loop_interval=0)
    sched.pause()
    sched.generate_due_schedules()
    result = asyncio.run(sched.tick())
    assert calls == []
    assert result == {"dispatched": 0}


def test_paused_state_persists_across_scheduler_instances(engine):
    first = Scheduler(engine, settings=None, loop_interval=0)
    assert first.paused is False

    first.pause()
    with Session(engine) as session:
        setting = session.get(AppSetting, "scheduler.paused")
        assert setting is not None
        assert setting.value == "1"

    second = Scheduler(engine, settings=None, loop_interval=0)

    async def start_and_stop() -> None:
        second.start()
        assert second.paused is True
        await second.stop()

    asyncio.run(start_and_stop())

    second.resume()
    third = Scheduler(engine, settings=None, loop_interval=0)
    third.load_persisted_state()
    assert third.paused is False


# ---------------------------------------------------------------- 端到端（真实路由调用 run_send）


def test_auto_scan_interval_persists_across_scheduler_instances(engine):
    first = Scheduler(engine, settings=None, auto_scan_interval_hours=24, loop_interval=0)
    assert first.auto_scan_interval_hours == 24

    first.update_auto_scan_interval(1)
    assert first.auto_scan_interval_hours == 1
    with Session(engine) as session:
        setting = session.get(AppSetting, "scheduler.auto_scan_interval_hours")
        assert setting is not None
        assert setting.value == "1"

    second = Scheduler(engine, settings=None, auto_scan_interval_hours=24, loop_interval=0)
    second.load_persisted_state()
    assert second.auto_scan_interval_hours == 1

    with pytest.raises(ValueError):
        first.update_auto_scan_interval(0)
    with pytest.raises(ValueError):
        first.update_auto_scan_interval(169)


def test_scheduler_end_to_end_sends_via_run_send(settings):
    """调度触发后复用 browser_manager.run_send 真实发送一名好友。"""
    app = make_app(settings, [FakePage(logged_in=True, conversation_titles=("好友A",), right_title="好友A")])
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        create_account(client)
        tmpl = client.post(
            "/api/templates", json={"name": "T", "content": "{{target_name}}"}
        ).json()
        account_id = client.get("/api/accounts").json()[0]["id"]
        target = client.post(
            "/api/targets",
            json={"accountId": account_id, "nicknameSnapshot": "好友A", "templateId": tmpl["id"]},
        ).json()

        # 账号置为 HEALTHY 才允许派发
        with Session(app.state.engine) as session:
            from app.models import Account as AccountModel

            acc = session.get(AccountModel, account_id)
            acc.status = AccountStatus.HEALTHY
            session.add(acc)
            session.commit()

        # 独立调度器实例 + 复用 browser_manager.run_send（假浏览器）
        import random

        clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
        sched = Scheduler(
            app.state.engine,
            settings,
            send_account=app.state.browser_manager.run_send,
            clock=clock,
            sleep=asyncio.sleep,
            rng=random.Random(3),
            loop_interval=0,
            window_expiry_policy="dispatch",
        )
        sched.generate_due_schedules()

        async def drive():
            await sched.tick()
            await sched.wait_idle()

        asyncio.run(drive())

        with Session(app.state.engine) as session:
            jobs = session.exec(select(SendJob)).all()
            assert len(jobs) == 1
            assert jobs[0].status == JobStatus.SUCCEEDED


# ---------------------------------------------------------------- 通知挂接


class RecordingNotifier:
    """测试观测通知：记录每次 send。"""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.enabled = True

    async def send(self, title: str, content: str, level: str = "active") -> bool:
        self.sent.append((title, content, level))
        return True


def test_partial_result_triggers_notification(engine):
    api = make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {"overall": "partial"}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,  # 重试耗尽后才转终态并通知
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    assert len(notifier.sent) >= 1
    title, _, level = notifier.sent[0]
    assert "未确认" in title or "复核" in title
    assert level in ("timeSensitive", "critical")


def test_auth_expired_triggers_notification(engine):
    api = make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {"overall": "auth_expired"}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    assert any("登录失效" in title for title, _, _ in notifier.sent)


# ---------------------------------------------------------------- 会话状态自动同步


def test_auto_scan_status_exposes_running_task_id(engine):
    """自动同步运行时向前端暴露当前 ScanTask，便于任务坞接管进度。"""
    account = make_account(engine)
    with Session(engine) as session:
        session.add(ContractTarget(account_id=account.id, nickname_snapshot="好友A"))
        session.commit()

    clock = make_clock(datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc))

    async def drive() -> str:
        started = asyncio.Event()
        release = asyncio.Event()

        async def scan(account_id: str) -> dict:
            stamp = clock().replace(tzinfo=None)
            with Session(engine) as session:
                session.add(
                    ScanTask(
                        id="automatic-scan-task",
                        account_id=account_id,
                        source="automatic",
                        status=ScanTaskStatus.RUNNING,
                        stage="滚动读取会话",
                        entries_json="[]",
                        started_at=stamp,
                        updated_at=stamp,
                    )
                )
                session.commit()
            started.set()
            await release.wait()
            return {"overall": "ok"}

        scheduler = Scheduler(
            engine,
            settings=None,
            scan_account=scan,
            clock=clock,
            sleep=asyncio.sleep,
            loop_interval=0,
        )
        await scheduler.tick()
        await started.wait()
        current = scheduler.auto_scan_status()[0]
        assert current["status"] == "running"
        assert current["task_id"] == "automatic-scan-task"
        release.set()
        await scheduler.wait_idle()
        return current["task_id"]

    assert asyncio.run(drive()) == "automatic-scan-task"


def test_auto_scan_dispatches_once_per_interval(engine):
    """自动扫描以最近一次完成时间为基准，每 24 小时最多触发一次。"""
    account = make_account(engine)
    with Session(engine) as session:
        session.add(ContractTarget(account_id=account.id, nickname_snapshot="好友A"))
        session.commit()

    clock = make_clock(datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc))
    calls: list[str] = []

    async def scan(account_id: str) -> dict:
        calls.append(account_id)
        stamp = clock().replace(tzinfo=None)
        with Session(engine) as session:
            session.add(
                ScanTask(
                    id=f"scan-{len(calls)}",
                    account_id=account_id,
                    status=ScanTaskStatus.COMPLETED,
                    total=1,
                    completed=1,
                    stage="扫描完成",
                    overall="ok",
                    entries_json="[]",
                    started_at=stamp,
                    finished_at=stamp,
                    updated_at=stamp,
                )
            )
            session.commit()
        return {"overall": "ok"}

    scheduler = Scheduler(
        engine,
        settings=None,
        scan_account=scan,
        auto_scan_interval_hours=24,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
    )

    async def drive() -> None:
        await scheduler.tick()
        await scheduler.wait_idle()

    asyncio.run(drive())
    assert calls == [account.id]
    first_status = scheduler.auto_scan_status()[0]
    assert first_status["status"] == "scheduled"
    assert first_status["next_scan_at"] == datetime(2026, 8, 28, 10, 0)

    clock.set(datetime(2026, 8, 28, 9, 59, 59, tzinfo=timezone.utc))
    asyncio.run(drive())
    assert calls == [account.id]

    clock.set(datetime(2026, 8, 28, 10, 0, 1, tzinfo=timezone.utc))
    asyncio.run(drive())
    assert calls == [account.id, account.id]


def test_auto_scan_waits_until_send_window_ends(engine):
    """发送窗口及前 15 分钟保护区内不抢占账号浏览器，窗口结束后再触发。"""
    account = make_account(engine)
    with Session(engine) as session:
        session.add(ContractTarget(account_id=account.id, nickname_snapshot="好友A"))
        session.commit()

    clock = make_clock(datetime(2026, 8, 27, 11, 50, tzinfo=timezone.utc))
    calls: list[str] = []

    async def scan(account_id: str) -> dict:
        calls.append(account_id)
        return {"overall": "ok"}

    scheduler = Scheduler(
        engine,
        settings=None,
        scan_account=scan,
        auto_scan_interval_hours=24,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
    )

    async def drive() -> None:
        await scheduler.tick()
        await scheduler.wait_idle()

    # 19:50 Asia/Shanghai，处于 19:45-22:00 的自动扫描保护区。
    asyncio.run(drive())
    assert calls == []

    clock.set(datetime(2026, 8, 27, 14, 1, tzinfo=timezone.utc))
    asyncio.run(drive())
    assert calls == [account.id]


def test_auto_scan_conflict_is_shortly_deferred(engine):
    """撞上手动账号流程时只延后 5 分钟，不把本次同步误记成失败。"""
    account = make_account(engine)
    with Session(engine) as session:
        session.add(ContractTarget(account_id=account.id, nickname_snapshot="好友A"))
        session.commit()

    class AccountBusyError(Exception):
        status_code = 409

    clock = make_clock(datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc))
    calls: list[str] = []

    async def scan(account_id: str) -> dict:
        calls.append(account_id)
        if len(calls) == 1:
            raise AccountBusyError()
        return {"overall": "ok"}

    scheduler = Scheduler(
        engine,
        settings=None,
        scan_account=scan,
        auto_scan_interval_hours=24,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
    )

    async def drive() -> None:
        await scheduler.tick()
        await scheduler.wait_idle()

    asyncio.run(drive())
    assert calls == [account.id]
    assert scheduler.auto_scan_status()[0]["next_scan_at"] == datetime(
        2026, 8, 27, 10, 5
    )

    clock.set(datetime(2026, 8, 27, 10, 4, 59, tzinfo=timezone.utc))
    asyncio.run(drive())
    assert calls == [account.id]

    clock.set(datetime(2026, 8, 27, 10, 5, tzinfo=timezone.utc))
    asyncio.run(drive())
    assert calls == [account.id, account.id]
