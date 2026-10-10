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
from app.scheduler import Scheduler, format_name_list, summarize_send_results
from tests.fakes import FakePage, RecordingNotifier, create_account, make_app


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


def test_fixed_time_grace_persists_across_scheduler_instances(engine):
    """宽限期写库持久化：新实例恢复，避免页面上的设置重启后悄悄回退。"""
    first = Scheduler(engine, settings=None, loop_interval=0)
    assert first.fixed_time_grace_minutes == 30
    first.update_fixed_time_grace_minutes(5)
    assert first.fixed_time_grace_minutes == 5
    with Session(engine) as session:
        setting = session.get(AppSetting, "scheduler.fixed_time_grace_minutes")
        assert setting is not None and setting.value == "5"

    second = Scheduler(engine, settings=None, loop_interval=0)
    second.load_persisted_state()
    assert second.fixed_time_grace_minutes == 5

    with pytest.raises(ValueError):
        second.update_fixed_time_grace_minutes(-1)
    with pytest.raises(ValueError):
        second.update_fixed_time_grace_minutes(1441)


def test_scheduled_always_send_toggle_persists_and_applies_immediately(engine):
    """「到点重发」开关：写库持久化、新实例恢复、且下一次 tick 立即按新值派发。"""
    account = make_account(engine, send_time_hhmm="21:30")
    seen: list[str | None] = []

    async def send(account_id: str, *, exclude_scheduled: bool = True, force_run_id=None) -> dict:
        seen.append(force_run_id)
        return {"overall": "ok", "results": []}

    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))

    def build() -> Scheduler:
        return Scheduler(
            engine,
            settings=None,
            send_account=send,
            clock=clock,
            sleep=asyncio.sleep,
            loop_interval=0,
            retry_max_count=0,
            window_expiry_policy="dispatch",
        )

    first = build()
    assert first.scheduled_always_send is False
    first.update_scheduled_always_send(True)
    assert first.scheduled_always_send is True
    with Session(engine) as session:
        setting = session.get(AppSetting, "scheduler.scheduled_always_send")
        assert setting is not None and setting.value == "1"

    # 关掉开关后立刻回到「不带固定槽位」的每日一次语义
    first.update_scheduled_always_send(False)
    first.generate_due_schedules()
    asyncio.run(first.tick())
    asyncio.run(first.wait_idle())
    assert seen == [None], "关闭后不应再传固定槽位"

    # 新实例从数据库恢复持久化值
    second = build()
    first.update_scheduled_always_send(True)
    third = Scheduler(engine, settings=None, loop_interval=0)
    third.load_persisted_state()
    assert third.scheduled_always_send is True
    assert second.scheduled_always_send is False


def test_account_exact_time_sent_within_grace_period(engine):
    """回归：固定时刻本身是「窗口终点」，没有宽限期就会在到点那一刻被判过期。

    这里时钟设在固定时刻之前几毫秒（模拟 tick 对齐边界的真实情况），计划必须派发，
    而不是被 `local_now >= fixed` 判成「窗口已结束」。
    """
    account = make_account(engine, send_time_hhmm="21:30")
    calls: list[str] = []

    async def send(account_id: str, **kwargs) -> dict:
        calls.append(account_id)
        return {"overall": "ok"}

    # 21:30 Asia/Shanghai = 13:30 UTC
    for offset in (
        timedelta(milliseconds=-2),  # 到点前几毫秒：tick 提前醒来的真实情况
        timedelta(0),  # 正好到点
        timedelta(minutes=29),  # 宽限期内（默认 30 分钟）
    ):
        calls.clear()
        with Session(engine) as session:
            for row in session.exec(select(SendSchedule)).all():
                session.delete(row)
            session.commit()
        clock = make_clock(datetime(2026, 8, 27, 13, 30, tzinfo=timezone.utc) + offset)
        sched = Scheduler(
            engine,
            settings=None,
            send_account=send,
            clock=clock,
            sleep=asyncio.sleep,
            loop_interval=0,
            retry_max_count=0,
            window_expiry_policy="skip",
        )
        sched.generate_due_schedules()
        asyncio.run(sched.tick())
        asyncio.run(sched.wait_idle())
        assert calls == [account.id], (
            f"距固定时刻 {offset} 时应在 skip 策略下照常派发"
        )
        assert schedule_rows(engine)[0].status != ScheduleStatus.SKIPPED


def test_account_exact_time_skipped_after_grace_period(engine):
    """超过宽限期仍未派发 → 转 SKIPPED，保持「错过就不深夜补发」的原意。"""
    make_account(engine, send_time_hhmm="21:30")
    calls: list[str] = []

    async def send(account_id: str, **kwargs) -> dict:
        calls.append(account_id)
        return {"overall": "ok"}

    # 固定 21:30（13:30 UTC），时钟 14:05 UTC（本地 22:05）= 超出 30 分钟宽限
    clock = make_clock(datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc))
    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="skip",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    assert calls == []
    assert schedule_rows(engine)[0].status == ScheduleStatus.SKIPPED


def test_account_exact_time_skipped_when_already_past(engine):
    """账号设置固定发送时刻后，该时刻已过仍未派发 → 今天不再补发，转 SKIPPED。"""
    # 固定 21:30（= 13:30 UTC）；时钟设在 15:00 UTC（本地 23:00）已过该时刻
    make_account(engine, send_time_hhmm="21:30")
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
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
        loop_interval=0,
    )
    sched.generate_due_schedules()  # 计划 = 21:30
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.SKIPPED  # 不补发
    assert calls == []  # 发送执行器未被调用


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


def test_scheduled_always_send_passes_stable_daily_slot(engine):
    """开启 scheduled_always_send：定时派发按业务日传固定槽位后缀。"""
    make_account(engine, send_time_hhmm="21:30")
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    seen: list[str | None] = []

    async def send(account_id: str, *, exclude_scheduled: bool = True, force_run_id=None) -> dict:
        seen.append(force_run_id)
        return {"overall": "ok"}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="dispatch",
        scheduled_always_send=True,
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    # 21:30 Asia/Shanghai = 13:30 UTC，clock 设在 15:00 UTC 已过该时刻（dispatch 策略仍派发）
    assert seen == ["sched20260827"]
    assert schedule_rows(engine)[0].status == ScheduleStatus.SUCCEEDED


def test_scheduled_send_default_keeps_daily_once(engine):
    """默认（不开 scheduled_always_send）：定时派发不带固定槽位，保持每日一次防重复。"""
    make_account(engine, send_time_hhmm="21:30")
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    seen: list[str | None] = []

    async def send(account_id: str, *, exclude_scheduled: bool = True, force_run_id=None) -> dict:
        seen.append(force_run_id)
        return {"overall": "ok"}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    assert seen == [None]


def test_seconds_until_next_tick_aligns_to_minute_boundary(engine):
    """节拍对齐整分钟：否则固定 02:22 会被进程启动相位带着漂移到 :25 之类。"""
    clock = make_clock(datetime(2026, 8, 27, 13, 17, 25, tzinfo=timezone.utc))
    sched = Scheduler(engine, settings=None, clock=clock, loop_interval=0)
    # 余量让醒来时刻落在边界之后（定时器可能早几毫秒返回）
    assert 35.0 <= sched._seconds_until_next_tick() < 35.1


def test_exact_time_target_dispatched_when_tick_lands_milliseconds_early(engine):
    """回归：整分边界前几毫秒醒来时，到点好友仍要发，而不是整整推迟一分钟。

    实测事件循环的定时器会早若干毫秒返回，tick 落在 :59.998 之类的时刻；若按
    `due_at > now` 严格比较，14:01 的计划会被判成「还没到点」，14:02 才发。
    """
    account = make_account(engine, send_time_hhmm="21:30")
    with Session(engine) as session:
        session.add(
            ContractTarget(account_id=account.id, nickname_snapshot="准点好友", send_time_hhmm="21:30")
        )
        session.commit()

    calls: list[list[str]] = []

    async def send_targets(account_id: str, ids: list[str], *, force_run_id=None) -> dict:
        calls.append(ids)
        return {"overall": "ok", "results": []}

    # 本地 21:30 = 13:30 UTC；时钟设在到点前 2 毫秒
    clock = make_clock(datetime(2026, 8, 27, 13, 29, 59, 998000, tzinfo=timezone.utc))
    sched = Scheduler(
        engine,
        settings=None,
        send_targets=send_targets,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="dispatch",
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    assert len(calls) == 1, "到点前几毫秒醒来时仍应派发，不应推迟一分钟"


def test_account_plan_dispatched_when_tick_lands_milliseconds_early(engine):
    """同上：账号级计划的到点比较也要有容差，否则整分钟被推迟。"""
    make_account(engine, send_time_hhmm="21:30")
    calls: list[str] = []

    async def send(account_id: str) -> dict:
        calls.append(account_id)
        return {"overall": "ok"}

    clock = make_clock(datetime(2026, 8, 27, 13, 29, 59, 998000, tzinfo=timezone.utc))
    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    assert len(calls) == 1, "到点前几毫秒醒来时账号批量仍应派发"


def test_exact_time_target_dispatched_before_account_batch(engine):
    """同一轮 tick 里好友级准点先发、账号批量随后：准点好友不被批量挤到下一轮。"""
    account = make_account(engine, send_time_hhmm="02:22")
    with Session(engine) as session:
        session.add(
            ContractTarget(account_id=account.id, nickname_snapshot="准点好友", send_time_hhmm="02:22")
        )
        session.add(ContractTarget(account_id=account.id, nickname_snapshot="普通好友"))
        session.commit()

    order: list[str] = []

    async def send_account(account_id: str, *, exclude_scheduled: bool = True, force_run_id=None) -> dict:
        order.append(f"账号批量:exclude_scheduled={exclude_scheduled}")
        return {"overall": "ok", "results": []}

    async def send_targets(account_id: str, ids: list[str], *, force_run_id=None) -> dict:
        with Session(engine) as session:
            names = [session.get(ContractTarget, i).nickname_snapshot for i in ids]
        order.append(f"好友级:{names}")
        return {"overall": "ok", "results": []}

    # 本地 2026-08-28 02:22:05（账号时区 Asia/Shanghai）→ 规划时刻已到
    clock = make_clock(datetime(2026, 8, 27, 18, 22, 5, tzinfo=timezone.utc))
    sched = Scheduler(
        engine,
        settings=None,
        send_account=send_account,
        send_targets=send_targets,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    assert order == ["好友级:['准点好友']", "账号批量:exclude_scheduled=True"]
    assert schedule_rows(engine)[0].status == ScheduleStatus.SUCCEEDED


def test_scheduled_always_send_uses_stable_slot_for_exact_time_target(engine):
    """开启 scheduled_always_send：好友级准点派发按业务日传固定槽位（当天发过也照发）。"""
    account = make_account(engine, send_time_hhmm="02:22")
    with Session(engine) as session:
        session.add(
            ContractTarget(account_id=account.id, nickname_snapshot="准点好友", send_time_hhmm="02:22")
        )
        session.commit()

    seen: list[str | None] = []

    async def send_targets(account_id: str, ids: list[str], *, force_run_id=None) -> dict:
        seen.append(force_run_id)
        return {"overall": "ok", "results": []}

    clock = make_clock(datetime(2026, 8, 27, 18, 22, 5, tzinfo=timezone.utc))
    sched = Scheduler(
        engine,
        settings=None,
        send_targets=send_targets,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="dispatch",
        scheduled_always_send=True,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    assert seen == ["sched20260828"]


# ---------------------------------------------------------------- 通知挂接


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
    """发送前登录判定确认为失效（账号已被标记）时，终局并 critical 提醒重新扫码。

    注意：只有账号状态真被翻成 AUTH_EXPIRED 才算「确认失效」。单次判定未通过时
    账号仍 HEALTHY，按可重试处理且不催用户重新扫码（见 test_unconfirmed_...）。
    """
    api = make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        # 模拟本次判定达到容忍次数：账号在发送过程中被标记失效
        _set_account_status(engine, account_id, AccountStatus.AUTH_EXPIRED)
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


# ---------------------------------------------------------------- 通知内容与级别


def test_summarize_send_results_separates_skipped_from_failures():
    """skipped 是「今日已发」的正常幂等结果，不能和真正需要人工介入的失败混为一谈。"""
    ok, skipped, failed = summarize_send_results(
        [
            {"target": "A", "status": "succeeded"},
            {"target": "B", "status": "duplicate_succeeded"},
            {"target": "C", "status": "skipped"},
            {"target": "D", "status": "not_found"},
            {"target": "E", "status": "unknown_requires_review"},
        ]
    )
    assert ok == 2
    assert skipped == ["C"]
    assert failed == ["D", "E"]


def test_format_name_list_truncates_long_lists():
    assert format_name_list([]) == ""
    assert format_name_list(["A", "B"]) == "A、B"
    long_list = [f"F{i}" for i in range(8)]
    assert format_name_list(long_list).endswith("等 8 人")


def test_success_notification_reports_skipped_and_counts(engine):
    """成功汇总要说清「成功几个、跳过几个」，而不是只给一个 N/M。"""
    make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {
            "overall": "ok",
            "results": [
                {"target": "A", "status": "succeeded"},
                {"target": "B", "status": "skipped"},
            ],
        }

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

    title, content, level = notifier.sent[-1]
    assert title == "今日续焰完成"
    assert "成功 1/2" in content
    assert "跳过 1" in content
    assert level == "active"


def test_success_notification_can_be_disabled(engine):
    make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {"overall": "ok", "results": [{"target": "A", "status": "succeeded"}]}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        notify_on_success=False,
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    assert notifier.sent == []


def _make_exact_time_target(engine, name: str = "示例好友", hhmm: str = "09:00") -> None:
    """建一个设了独立发送时刻的好友，供好友级准点派发测试复用。"""
    with Session(engine) as session:
        account = session.exec(select(Account)).first()
        session.add(
            ContractTarget(
                account_id=account.id, nickname_snapshot=name, send_time_hhmm=hhmm
            )
        )
        session.commit()


def _add_target(engine, name: str = "普通好友") -> None:
    """建一个普通好友（无独立时刻）；自动扫描要求账号至少有一个契约对象。"""
    with Session(engine) as session:
        account = session.exec(select(Account)).first()
        session.add(ContractTarget(account_id=account.id, nickname_snapshot=name))
        session.commit()


def _set_account_status(engine, account_id: str, status: AccountStatus) -> None:
    with Session(engine) as session:
        account = session.get(Account, account_id)
        account.status = status
        session.add(account)
        session.commit()


async def _noop_scan(account_id: str) -> dict:
    """占位扫描执行器：只让调度器认为「已配置扫描器」，本测试不真正触发扫描。"""
    return {"overall": "ok", "entries": []}


def test_target_send_success_notifies_with_friend_name(engine):
    """好友级准点发送成功也要回音，并且点名是谁——无人值守时最关心的就是这一条。"""
    make_account(engine)
    _make_exact_time_target(engine)
    # 09:00 Asia/Shanghai = 01:00 UTC
    clock = make_clock(datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send_targets(account_id: str, target_ids: list[str]) -> dict:
        return {"overall": "ok", "results": [{"target": "示例好友", "status": "succeeded"}]}

    sched = Scheduler(
        engine,
        settings=None,
        send_targets=send_targets,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    assert notifier.sent, "准点发送成功必须推送"
    title, content, level = notifier.sent[-1]
    assert "准点" in title
    assert "示例好友" in content
    assert level == "active"


def test_target_send_failure_notifies_with_friend_name(engine):
    """准点发送失败要说清是谁、卡在哪，而不是笼统的「好友级定时发送未成功」。"""
    make_account(engine)
    _make_exact_time_target(engine)
    clock = make_clock(datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send_targets(account_id: str, target_ids: list[str]) -> dict:
        return {
            "overall": "partial",
            "results": [{"target": "示例好友", "status": "unknown_requires_review"}],
        }

    sched = Scheduler(
        engine,
        settings=None,
        send_targets=send_targets,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    title, content, level = notifier.sent[-1]
    assert "未完成" in title
    assert "示例好友" in content
    assert "未成功" in content
    assert level == "timeSensitive"


def test_send_error_notification_uses_critical_level(engine):
    """回归：发送出错曾传非法 level "errors"，被静默降级成最低优先级。

    「账号发送出错」是需要立刻处理的通知，必须是 critical。
    """
    make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {"overall": "error", "detail": "DomChangedError"}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    title, content, level = notifier.sent[-1]
    assert "出错" in title
    assert level == "critical"
    assert "DomChangedError" in content  # 失败原因要带上，便于判断该做什么


def test_network_unavailable_notification_uses_critical_level(engine):
    """回归：网络持续不可用同样曾传非法 level "errors"。"""
    make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {"overall": "network_unavailable"}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        network_max_deferrals=0,  # 第一轮即判定持续不可用
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    title, content, level = notifier.sent[-1]
    assert "网络" in title
    assert level == "critical"


def test_partial_notification_lists_unconfirmed_friends(engine):
    """部分未确认的推送要列出具体好友，人工核对时不必再去任务记录里翻。"""
    make_account(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {
            "overall": "partial",
            "results": [
                {"target": "张三", "status": "unknown_requires_review"},
                {"target": "李四", "status": "ambiguous"},
            ],
        }

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=0,
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    title, content, level = notifier.sent[-1]
    assert "未确认" in title
    assert "张三" in content and "李四" in content
    assert level == "timeSensitive"


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


# ---------------------------------------------------------------- 登录判定容忍


def test_unconfirmed_auth_failure_is_retryable_not_terminal(engine):
    """发送前登录判定未通过、但账号未被标记失效时：不当终局，也不催用户重新扫码。

    回归背景：调度器对非 HEALTHY 账号会跳过扫描与发送，若把单次判定抖动当终局，
    账号会停摆且无法自愈（只能人工重新扫码）——2026-10-10 实测发生过。
    """
    make_account(engine)  # HEALTHY
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {"overall": "auth_expired", "detail": "登录判定未通过，但未确认失效"}

    sched = Scheduler(
        engine,
        settings=None,
        send_account=send,
        notifier=notifier,
        clock=clock,
        sleep=asyncio.sleep,
        loop_interval=0,
        retry_max_count=1,
        window_expiry_policy="dispatch",
    )
    sched.generate_due_schedules()
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    assert not any("登录失效" in title for title, _, _ in notifier.sent)
    rows = schedule_rows(engine)
    assert rows and rows[0].status == ScheduleStatus.PENDING  # 未终局，可在窗口内重试


def test_auto_scan_unconfirmed_auth_failure_does_not_notify(engine):
    """自动同步单次判定失败：不推送，账号保持 HEALTHY，下一轮自动重试。"""
    account = make_account(engine)
    _add_target(engine)
    notifier = RecordingNotifier()

    async def scan(account_id: str) -> dict:
        return {"overall": "auth_expired", "entries": []}

    sched = Scheduler(
        engine,
        settings=None,
        scan_account=scan,
        notifier=notifier,
        clock=make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc)),
        sleep=asyncio.sleep,
        loop_interval=0,
    )
    asyncio.run(sched._run_auto_scan(account.id))

    assert notifier.sent == []


def test_auto_scan_confirmed_auth_failure_notifies(engine):
    """账号确已被判定失效时，自动同步照旧 critical 提醒。"""
    account = make_account(engine, status=AccountStatus.AUTH_EXPIRED)
    _add_target(engine)
    notifier = RecordingNotifier()

    async def scan(account_id: str) -> dict:
        return {"overall": "auth_expired", "entries": []}

    sched = Scheduler(
        engine,
        settings=None,
        scan_account=scan,
        notifier=notifier,
        clock=make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc)),
        sleep=asyncio.sleep,
        loop_interval=0,
    )
    asyncio.run(sched._run_auto_scan(account.id))

    titles = [title for title, _, _ in notifier.sent]
    assert any("会话状态同步失败" in title for title in titles)


def test_auto_scan_retries_immediately_after_unconfirmed_failure(engine):
    """未确认失效时账号仍是 HEALTHY，因此会被判定「应当再扫一次」——即快速复核，
    而不必等满一个同步周期。"""
    account = make_account(engine)
    _add_target(engine)
    clock = make_clock(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
    with Session(engine) as session:
        session.add(
            ScanTask(
                id="scan-1",
                account_id=account.id,
                status=ScanTaskStatus.FAILED,
                overall="auth_expired",
                detail="登录判定未通过，但未确认失效",
            )
        )
        session.commit()

    sched = Scheduler(
        engine, settings=None, scan_account=_noop_scan, clock=clock,
        sleep=asyncio.sleep, loop_interval=0,
    )
    assert sched._auto_scan_due(account, clock()) is True
