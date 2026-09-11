"""M11 扫描对账：纯分类、持久化快照与历史查询。"""

import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import Account, ContractTarget, ScanTask, ScanTaskStatus, User
from app.scan_reconciliation import build_reconciliation
from tests.fakes import FakePage, make_app
from tests.test_send import _account_with_targets


def _previous(days: int | None, label: str | None = None) -> dict:
    return {
        "days": days,
        "kind": "normal" if days is not None else None,
        "label": label,
        "scannedAt": "2026-09-10T10:00:00+00:00" if days is not None else None,
    }


def _target(
    target_id: str,
    title: str,
    *,
    days: int | None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    conversation_type: str | None = "user",
) -> dict:
    return {
        "targetId": target_id,
        "title": title,
        "conversationType": conversation_type,
        "platformUserId": user_id,
        "platformConversationId": conversation_id,
        "previous": _previous(days),
    }


def _entry(
    title: str,
    *,
    days: int | None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    conversation_type: str | None = "user",
    preview: str = "",
) -> dict:
    return {
        "title": title,
        "platform_user_id": user_id,
        "platform_conversation_id": conversation_id,
        "conversation_type": conversation_type,
        "streak_days": days,
        "streak_kind": "normal" if days is not None else None,
        "streak_label": None,
        "preview": preview,
    }


def test_build_reconciliation_covers_five_categories_and_strict_matching():
    targets = [
        _target("changed-id", "状态变化", days=5),
        _target("no-spark-id", "熄火好友", days=8),
        _target("same-id", "无变化", days=3),
        _target("missing-id", "未观察好友", days=2),
        _target("group-id", "群聊", days=10, user_id=None, conversation_id="conv-1", conversation_type="group"),
    ]
    entries = [
        _entry("状态变化", days=6),
        _entry("熄火好友", days=None),
        _entry("无变化", days=3),
        _entry("群聊", days=11, conversation_id="conv-1", conversation_type="group"),
        _entry("新会话", days=4),
    ]

    result = build_reconciliation(targets, entries)

    assert result["counts"] == {
        "changed": 2,
        "no_spark": 1,
        "new": 1,
        "not_observed": 1,
        "unchanged": 1,
    }
    by_category = {item["category"]: item for item in result["entries"]}
    changed = [item for item in result["entries"] if item["category"] == "changed"]
    assert {item["previous"]["days"] for item in changed} == {5, 10}
    assert {item["current"]["days"] for item in changed} == {6, 11}
    assert by_category["no_spark"]["current"]["label"] == "无火花"
    assert by_category["new"]["targetId"] is None
    assert by_category["not_observed"]["current"]["days"] is None


def test_reconciliation_prefers_stable_user_and_group_ids_over_nickname():
    targets = [
        _target("user-target", "重名用户", days=1, user_id="uid-a"),
        _target(
            "group-target",
            "同名群",
            days=1,
            conversation_id="conv-a",
            conversation_type="group",
        ),
    ]
    result = build_reconciliation(
        targets,
        [
            _entry("重名用户", days=2, user_id="uid-a"),
            # Same title but a different stable user is not the saved target.
            _entry("重名用户", days=9, user_id="uid-b"),
            _entry("同名群", days=2, conversation_id="conv-a", conversation_type="group"),
            # Different group ID cannot fall back to the same title.
            _entry("同名群", days=9, conversation_id="conv-b", conversation_type="group"),
        ],
    )
    assert result["counts"]["changed"] == 2
    assert result["counts"]["new"] == 2
    assert result["counts"]["not_observed"] == 0
    new_titles = [item["title"] for item in result["entries"] if item["category"] == "new"]
    assert new_titles == ["重名用户", "同名群"]


def test_reconciliation_is_persisted_before_manual_sync_and_exposed_in_progress(settings):
    app = make_app(settings, [FakePage(logged_in=True)])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        manager = app.state.browser_manager

        async def fake_scan(account_id, progress=None):
            return {
                "overall": "ok",
                "detail": None,
                "entries": [_entry("好友A", days=6), _entry("新会话", days=2)],
            }

        manager._scan_chats_impl = fake_scan
        started = client.post(f"/api/accounts/{account['id']}/scan-chats/start")
        assert started.status_code == 202, started.text
        task_id = started.json()["taskId"]
        for _ in range(100):
            snapshot = client.get(
                f"/api/accounts/{account['id']}/scan-chats/progress",
                params={"taskId": task_id},
            ).json()
            if snapshot["status"] != "running":
                break
            time.sleep(0.01)
        assert snapshot["status"] == "completed", snapshot
        assert snapshot["reconciliation"]["counts"]["changed"] == 1
        assert snapshot["reconciliation"]["counts"]["new"] == 1
        assert snapshot["reconciliation"]["counts"]["notObserved"] == 0

        # The current task has not performed the frontend's manual status sync
        # yet, so the target still carries its previous official snapshot.
        target = client.get("/api/targets", params={"accountId": account["id"]}).json()[0]
        assert target["sparkDays"] is None


def test_failed_scan_has_no_not_observed_reconciliation(settings):
    app = make_app(settings, [FakePage(logged_in=True)])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        manager = app.state.browser_manager

        async def failed_scan(_account_id, progress=None):
            return {"overall": "error", "detail": "network", "entries": []}

        manager._scan_chats_impl = failed_scan
        started = client.post(f"/api/accounts/{account['id']}/scan-chats/start").json()
        for _ in range(100):
            snapshot = client.get(
                f"/api/accounts/{account['id']}/scan-chats/progress",
                params={"taskId": started["taskId"]},
            ).json()
            if snapshot["status"] != "running":
                break
            time.sleep(0.01)
        assert snapshot["status"] == "failed"
        assert snapshot["reconciliation"]["counts"]["notObserved"] == 0
        assert snapshot["reconciliation"]["entries"] == []


def test_scan_history_is_lightweight_sorted_limited_and_user_isolated(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        # Create three persisted tasks directly, including an old task with the
        # migration's empty default to prove it is not backfilled dynamically.
        with Session(app.state.engine) as session:
            now = datetime.now(timezone.utc)
            for index in range(3):
                session.add(
                    ScanTask(
                        account_id=account["id"],
                        source="manual" if index != 1 else "automatic",
                        status=ScanTaskStatus.COMPLETED,
                        total=1,
                        completed=1,
                        overall="ok",
                        entries_json='[{"title":"不应返回"}]',
                        reconciliation_json=(
                            '{"counts":{"changed":1,"no_spark":0,"new":0,"not_observed":0,"unchanged":0},'
                            '"entries":[]}'
                            if index == 0
                            else '{"counts":{"changed":0,"no_spark":0,"new":0,"not_observed":0,"unchanged":0},"entries":[]}'
                        ),
                        started_at=now - timedelta(hours=index),
                        finished_at=now - timedelta(hours=index),
                        updated_at=now - timedelta(hours=index),
                    )
                )
            session.commit()

        history = client.get(
            f"/api/accounts/{account['id']}/scan-chats/history",
            params={"limit": 2},
        )
        assert history.status_code == 200, history.text
        rows = history.json()
        assert len(rows) == 2
        assert rows[0]["reconciliation"]["counts"]["changed"] == 1
        assert "entries" not in rows[0]  # no full entries_json leakage
        assert rows[1]["reconciliation"]["counts"]["changed"] == 0

        assert client.get(
            f"/api/accounts/{account['id']}/scan-chats/history",
            params={"limit": 0},
        ).status_code == 422
        assert client.get(
            f"/api/accounts/{account['id']}/scan-chats/history",
            params={"limit": 21},
        ).status_code == 422

        # A second platform user cannot read this account's scan history.
        client.post(
            "/api/auth/users",
            json={"username": "other", "password": "other1234"},
        ).json()
        login = client.post(
            "/api/auth/login",
            json={"username": "other", "password": "other1234"},
        ).json()
        response = client.get(
            f"/api/accounts/{account['id']}/scan-chats/history",
            headers={"Authorization": f"Bearer {login['token']}"},
        )
        assert response.status_code == 404
