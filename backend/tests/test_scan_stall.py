"""回归：卡死的会话扫描不得永久占住账号的发送闸门。

2026-11-01 实测事故：账号「示例账号」的自动同步卡在「滚动读取会话」，
`_active_scan` 从此永远为真 → 02:22 的准点发送被 409 拒绝，而好友级准点派发是
按 tick（60 秒）重试的，于是手机连续收到 5 条「发送时中断（HTTPException）」，
2 小时内该账号的自动同步也再没有跑起来过。

修复分三层，这里逐层验证：
1. 扫描任务有总预算，超时先落终态、释放占用，再尽力取消浏览器侧操作；
2. 连超时收束都失效时，按启动时间判定「僵尸扫描」并就地失效；
3. 仍在预算内的正常扫描保持原有互斥，不能被顺手放行。
"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.core.database import build_engine
from app.core.errors import describe_exception
from app.core.migrations import run_migrations
from app.execution.browser_manager import BrowserManager, ScanProgress
from app.models import Account, ScanTask, ScanTaskStatus


def _account(engine) -> str:
    with Session(engine) as session:
        account = Account(display_name="卡死账号")
        session.add(account)
        session.commit()
        session.refresh(account)
        return account.id


def _manager(settings, *, scan_timeout: float = 0.1, stale_after: float = 100.0):
    engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)
    manager = BrowserManager(engine, settings, sleep=lambda _seconds: asyncio.sleep(0))
    # 直接压到秒级以下，避免单测为了等一个真实超时跑上十分钟。
    manager._scan_timeout = scan_timeout
    manager._scan_stale_after = stale_after
    return engine, manager


def test_hung_scan_times_out_and_releases_account(settings):
    """扫描体里的 await 永不返回时，超时也要先把「扫描中」占用放回。"""
    engine, manager = _manager(settings)
    account_id = _account(engine)

    async def hang_forever(_account_id, _progress=None):
        await asyncio.Event().wait()

    manager._scan_chats_impl = hang_forever

    async def scenario():
        started = await manager.start_scan_chats(account_id, source="automatic")
        assert manager._active_scan(account_id) is not None, "扫描开始时应占用闸门"
        await asyncio.sleep(0.4)
        return started

    started = asyncio.run(scenario())

    assert manager._active_scan(account_id) is None, "超时后必须放行发送"
    with Session(engine) as session:
        task = session.get(ScanTask, started["task_id"])
        assert task is not None
        assert task.status == ScanTaskStatus.FAILED
        assert task.stage == "扫描超时"
        assert task.finished_at is not None


def test_stale_scan_is_released_even_without_timeout_path(settings):
    """兜底：进程里遗留的 running 快照超过预算，不应再挡住发送。"""
    engine, manager = _manager(settings, scan_timeout=100.0, stale_after=60.0)
    account_id = _account(engine)

    progress = ScanProgress(
        task_id="stale-task",
        account_id=account_id,
        source="automatic",
        stage="滚动读取会话",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=600),
    )
    manager._scan_progress[progress.task_id] = progress

    assert manager._active_scan(account_id) is None
    assert progress.status == "failed"
    assert progress.stage == "扫描超时"


def test_running_scan_within_budget_still_blocks(settings):
    """没超时的扫描仍然占用账号——互斥不能被修复顺手抹掉。"""
    engine, manager = _manager(settings, scan_timeout=100.0, stale_after=60.0)
    account_id = _account(engine)

    progress = ScanProgress(
        task_id="live-task",
        account_id=account_id,
        source="automatic",
        stage="滚动读取会话",
        started_at=datetime.now(timezone.utc),
    )
    manager._scan_progress[progress.task_id] = progress

    assert manager._active_scan(account_id) is not None
    assert progress.status == "running"


def test_http_exception_detail_survives_into_user_facing_reason():
    """HTTPException 的中文 detail 才是用户能看懂的原因，不能被降级成类名。"""
    from fastapi import HTTPException

    exc = HTTPException(status_code=409, detail="账号正在扫描会话，暂不能发送")
    assert describe_exception(exc) == "账号正在扫描会话，暂不能发送"

    plain = describe_exception(RuntimeError("未配置发送执行器"))
    assert plain == "未配置发送执行器"

    assert describe_exception(ValueError()) == "ValueError"
