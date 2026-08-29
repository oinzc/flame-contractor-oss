"""M6-2：网络预检延迟、有界重试、发送冷却、窗口过期策略。"""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.execution.browser_manager import BrowserManager
from app.models import ScheduleStatus, SendJob
from app.scheduler import Scheduler
from tests.fakes import FakePage, create_account, make_app
from tests.test_diagnostics import _account_with_target
from tests.test_scheduler import RecordingNotifier, make_account, make_clock, schedule_rows


def _due_scheduler(engine, clock, **kwargs):
    """生成一个已到期的计划并返回 (scheduler, account)。

    默认 clock 设在窗口刚结束（14:30 UTC）并采用 dispatch 策略，保证「到期必派发」，
    专测重试/延迟语义；窗口策略本身的测试自行覆盖 clock 与策略。
    """
    account = make_account(engine)
    clock.set(datetime(2026, 8, 27, 14, 30, tzinfo=timezone.utc))
    kwargs.setdefault("window_expiry_policy", "dispatch")
    sched = Scheduler(engine, None, clock=clock, sleep=asyncio.sleep, loop_interval=0, **kwargs)
    sched.generate_due_schedules()
    return sched, account


def test_network_unavailable_defers_and_keeps_pending(engine):
    """网络不可用：延迟重试不消耗重试次数，计划保持 pending，不通知。"""
    clock = make_clock(datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()
    calls: list[str] = []

    async def send(account_id: str) -> dict:
        calls.append(account_id)
        return {"overall": "network_unavailable"}

    sched, _ = _due_scheduler(
        engine, clock, send_account=send, notifier=notifier,
        network_deferral_backoff=0, retry_backoff_base=0,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    assert calls  # 执行器被调用并返回不可用
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.PENDING  # 释放回待派发
    assert notifier.sent == []  # 未达上限不通知

    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.PENDING


def test_network_deferrals_exhaust_to_failed(engine):
    """连续多轮网络不可用：超过上限后终态 FAILED 并通知。"""
    clock = make_clock(datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {"overall": "network_unavailable"}

    sched, _ = _due_scheduler(
        engine, clock, send_account=send, notifier=notifier,
        network_max_deferrals=2, network_deferral_backoff=0, retry_backoff_base=0,
    )
    for _ in range(4):
        asyncio.run(sched.tick())
        asyncio.run(sched.wait_idle())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.FAILED
    assert any("网络" in title for title, _, _ in notifier.sent)


def test_error_retries_then_succeeds(engine):
    """执行器偶发异常：退避后自动重试并成功，不通知。"""
    clock = make_clock(datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()
    calls: list[str] = []

    async def send(account_id: str) -> dict:
        calls.append(account_id)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"overall": "ok"}

    sched, _ = _due_scheduler(
        engine, clock, send_account=send, notifier=notifier, retry_backoff_base=0,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.PENDING  # 第一次失败后释放待重试

    clock.set(datetime(2026, 8, 27, 14, 35, tzinfo=timezone.utc))
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.SUCCEEDED
    assert len(calls) == 2
    assert notifier.sent == []


def test_error_retries_exhaust_to_failed_with_notify(engine):
    """持续异常：重试耗尽后转 FAILED 并通知一次；不再重复派发。"""
    clock = make_clock(datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()
    calls: list[str] = []

    async def send(account_id: str) -> dict:
        calls.append(account_id)
        raise RuntimeError("boom")

    sched, _ = _due_scheduler(
        engine, clock, send_account=send, notifier=notifier,
        retry_max_count=1, retry_backoff_base=0,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    clock.set(datetime(2026, 8, 27, 14, 35, tzinfo=timezone.utc))
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())

    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.FAILED
    assert any("出错" in title for title, _, _ in notifier.sent)
    assert len(calls) == 2  # 1 次首发 + 1 次重试

    clock.set(datetime(2026, 8, 27, 14, 40, tzinfo=timezone.utc))
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    assert len(calls) == 2  # 已放弃，不再派发


def test_partial_notifies_only_after_retry_exhausted(engine):
    """partial：重试期间不打扰；耗尽后转待人工核并通知。"""
    clock = make_clock(datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()

    async def send(account_id: str) -> dict:
        return {"overall": "partial"}

    sched, _ = _due_scheduler(
        engine, clock, send_account=send, notifier=notifier,
        retry_max_count=1, retry_backoff_base=0,
    )
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    assert notifier.sent == []  # 重试期间静默

    clock.set(datetime(2026, 8, 27, 14, 35, tzinfo=timezone.utc))
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.UNKNOWN_REQUIRES_REVIEW
    assert any("未确认" in title for title, _, _ in notifier.sent)


def test_window_expired_skips_instead_of_late_send(engine):
    """窗口已结束：默认 skip 策略跳过计划并通知，绝不深夜打扰。"""
    clock = make_clock(datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc))
    notifier = RecordingNotifier()
    calls: list[str] = []

    async def send(account_id: str) -> dict:
        calls.append(account_id)
        return {"overall": "ok"}

    sched, _ = _due_scheduler(
        engine, clock, send_account=send, notifier=notifier,
        window_expiry_policy="skip",
    )
    clock.set(datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))  # 23:00 本地
    asyncio.run(sched.tick())
    asyncio.run(sched.wait_idle())
    rows = schedule_rows(engine)
    assert rows[0].status == ScheduleStatus.SKIPPED
    assert calls == []
    assert any("窗口已结束" in title for title, _, _ in notifier.sent)


def test_send_cooldown_between_targets(settings, engine):
    """多好友发送：好友之间插入冷却间隔（首轮不等待）。"""
    cooldown_calls: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        cooldown_calls.append(seconds)

    page = FakePage(
        logged_in=True,
        conversation_titles=("好友A", "好友B"),
        right_title="好友A",
    )
    manager = BrowserManager(
        engine,
        settings,
        browser_factory=_single_page_factory(page),
        login_poll_interval=0.02,
        login_check_timeout=0.2,
        send_timeout=0.2,
        send_poll_interval=0.05,
        send_stabilize_polls=2,
        confirm_timeout=0.2,
        search_timeout=0.2,
        send_cooldown=5.0,
        sleep=recording_sleep,
    )

    async def _online() -> bool:
        return True

    manager._network_checker = _online

    account = _make_account_with_two_targets(engine, settings)
    result = asyncio.run(manager.run_send(account.id))
    # 好友B 的右侧标题静态返回「好友A」会被判 ambiguous，不影响本测试关注的冷却断言
    assert result["overall"] in ("ok", "partial")
    assert len(result["results"]) == 2
    assert cooldown_calls.count(5.0) == 1  # 两个好友之间恰好一次冷却（其余为内部等待）


def _single_page_factory(page: FakePage):
    async def factory(_profile_path, _headless):
        return _FakeContextShim(), page

    return factory


class _FakeContextShim:
    async def close(self):
        return None


def _make_account_with_two_targets(engine, settings) -> object:
    from app.models import ContractTarget, MessageTemplate

    account = make_account(engine)
    with Session(engine) as session:
        template = MessageTemplate(name="T", content="{{target_name}}")
        session.add(template)
        session.commit()
        session.refresh(template)
        for nickname in ("好友A", "好友B"):  # 两位好友；好友B 会被判 ambiguous，不影响冷却断言
            session.add(
                ContractTarget(
                    account_id=account.id,
                    nickname_snapshot=nickname,
                    template_id=template.id,
                    streak_days=1,
                )
            )
        session.commit()
    return account
