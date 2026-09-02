"""会话扫描进度的 SQLite 持久化与重启收束测试。"""

import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.execution.browser_manager import BrowserManager
from app.main import create_app
from app.models import Account, ScanTask, ScanTaskStatus
from tests.fakes import FakePage, make_app
from tests.test_send import _account_with_targets


def _wait_scan(client: TestClient, account_id: str, task_id: str) -> dict:
    deadline = time.monotonic() + 5
    snapshot = client.get(
        f"/api/accounts/{account_id}/scan-chats/progress",
        params={"taskId": task_id},
    ).json()
    while snapshot["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = client.get(
            f"/api/accounts/{account_id}/scan-chats/progress",
            params={"taskId": task_id},
        ).json()
    return snapshot


def test_completed_scan_progress_is_readable_after_manager_recreation(settings):
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友A", "好友B"),
                titles_revealed_by_scroll=("好友C",),
            )
        ],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)
        started = client.post(f"/api/accounts/{account['id']}/scan-chats/start").json()
        finished = _wait_scan(client, account["id"], started["taskId"])
        assert finished["status"] == "completed", finished
        assert finished["source"] == "manual"
        assert finished["entries"][-1]["title"] == "好友C"

        reader = BrowserManager(build_engine(settings.database_url), settings)
        try:
            restored = reader.scan_chats_progress(account["id"], started["taskId"])
            latest = reader.latest_scan_chats(account["id"])
        finally:
            reader._engine.dispose()

        assert restored["status"] == "completed"
        assert [item["title"] for item in restored["entries"]] == [
            item["title"] for item in finished["entries"]
        ]
        assert latest is not None
        assert latest["task_id"] == started["taskId"]
        assert latest["source"] == "manual"


def test_automatic_scan_source_is_persisted_and_exposed(settings):
    engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)
    account = Account(display_name="自动同步账号")
    with Session(engine) as session:
        session.add(account)
        session.commit()
        session.refresh(account)

    manager = BrowserManager(engine, settings)

    async def hold_scan(_progress):
        await asyncio.Event().wait()

    manager._run_scan_task = hold_scan

    async def exercise() -> dict:
        started = await manager.start_scan_chats(account.id, source="automatic")
        await asyncio.sleep(0)
        with Session(engine) as session:
            stored = session.get(ScanTask, started["task_id"])
            assert stored is not None
            assert stored.source == "automatic"
        await manager.cancel_scan_chats(account.id, started["task_id"])
        return manager.latest_scan_chats(account.id) or {}

    try:
        latest = asyncio.run(exercise())
    finally:
        engine.dispose()

    assert latest["source"] == "automatic"


def test_running_scan_is_marked_interrupted_on_app_start(settings):
    engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)
    account = Account(display_name="主账号")
    task = ScanTask(
        account_id=account.id,
        status=ScanTaskStatus.RUNNING,
        completed=2,
        stage="滚动读取会话",
        current_item="好友B",
        round=3,
        entries_json=json.dumps(
            [{"title": "好友A", "preview": "", "platform_user_id": None}],
            ensure_ascii=False,
        ),
        started_at=datetime.now(timezone.utc),
    )
    with Session(engine) as session:
        session.add(account)
        session.commit()
        session.refresh(account)
        task.account_id = account.id
        session.add(task)
        session.commit()
        account_id = account.id
        task_id = task.id
    engine.dispose()

    manager_engine = build_engine(settings.database_url)
    manager = BrowserManager(manager_engine, settings)
    app = create_app(settings, browser_manager=manager)
    with TestClient(app) as client:
        latest = client.get(f"/api/accounts/{account_id}/scan-chats/latest")
        assert latest.status_code == 200, latest.text
        payload = latest.json()
        assert payload["status"] == "interrupted"
        assert payload["stage"] == "后端重启，中断扫描"
        assert payload["entries"][0]["title"] == "好友A"
        assert "重新发起" in payload["detail"]

        with Session(app.state.engine) as session:
            stored = session.get(ScanTask, task_id)
            assert stored is not None
            assert stored.status == ScanTaskStatus.INTERRUPTED
