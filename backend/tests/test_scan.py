"""会话列表扫描（好友批量导入候选）与好友换绑模板的测试。"""

import asyncio
import time

from fastapi.testclient import TestClient

from tests.fakes import FakePage, make_app
from tests.test_send import _account_with_targets


def test_scan_chats_collects_all_titles(settings):
    """登录态有效：滚动揭示懒加载标题并去重收集；账号 last_verified 更新。"""
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友A", "好友B"),
                titles_revealed_by_scroll=("好友C", "好友A"),
            )
        ],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        result = client.post(f"/api/accounts/{account['id']}/scan-chats")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["overall"] == "ok"
        # FakeItem 的 inner_text 只有名字单行：title 收齐，preview 为空
        assert [item["title"] for item in payload["entries"]] == ["好友A", "好友B", "好友C"]
        assert all(item["preview"] == "" for item in payload["entries"])

        updated = client.get("/api/accounts").json()[0]
        assert updated["lastVerifiedAt"] is not None


def test_scan_chats_requires_login(settings):
    """登录失效：overall=auth_expired，账号状态转 auth_expired。"""
    app = make_app(settings, [FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = _account_with_targets(client)

        result = client.post(f"/api/accounts/{account['id']}/scan-chats")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["overall"] == "auth_expired"
        assert payload["entries"] == []

        updated = client.get("/api/accounts").json()[0]
        assert updated["status"] == "auth_expired"


def test_scan_chats_background_progress_reaches_completion(settings):
    """后台扫描返回任务号，完成后进度快照包含真实结果与 100% 终态。"""
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

        started = client.post(f"/api/accounts/{account['id']}/scan-chats/start")
        assert started.status_code == 202, started.text
        initial = started.json()
        assert initial["taskId"]
        assert initial["accountId"] == account["id"]
        assert initial["status"] == "running"

        deadline = time.monotonic() + 5
        snapshot = initial
        while snapshot["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            response = client.get(
                f"/api/accounts/{account['id']}/scan-chats/progress",
                params={"taskId": initial["taskId"]},
            )
            assert response.status_code == 200, response.text
            snapshot = response.json()

        assert snapshot["status"] == "completed", snapshot
        assert snapshot["overall"] == "ok"
        assert snapshot["total"] == 3
        assert snapshot["completed"] == 3
        assert snapshot["entries"][-1]["title"] == "好友C"


def test_cancel_scan_preserves_discovered_entries(settings):
    """取消后台扫描只结束任务，不丢失取消前已经持久化的候选会话。"""
    app = make_app(settings, [FakePage(logged_in=True)])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        manager = app.state.browser_manager

        async def blocked_scan(account_id, progress=None):
            assert progress is not None
            manager._update_scan_progress(
                progress.task_id,
                stage="滚动读取会话",
                completed=1,
                current_item="好友A",
                round=2,
                entries=[
                    {
                        "title": "好友A",
                        "preview": "",
                        "streak_days": 3,
                        "streak_kind": "normal",
                        "streak_label": None,
                        "platform_user_id": "uid-a",
                    }
                ],
            )
            await asyncio.Event().wait()

        manager._scan_chats_impl = blocked_scan
        started = client.post(f"/api/accounts/{account['id']}/scan-chats/start")
        assert started.status_code == 202, started.text
        task_id = started.json()["taskId"]
        time.sleep(0.03)

        cancelled = client.post(
            f"/api/accounts/{account['id']}/scan-chats/cancel",
            params={"taskId": task_id},
        )
        assert cancelled.status_code == 200, cancelled.text
        payload = cancelled.json()
        assert payload["status"] == "cancelled"
        assert payload["completed"] == 1
        assert payload["entries"][0]["title"] == "好友A"
        assert "已保留已发现结果" in payload["detail"]

        latest = client.get(f"/api/accounts/{account['id']}/scan-chats/latest")
        assert latest.status_code == 200, latest.text
        assert latest.json()["status"] == "cancelled"
        assert latest.json()["entries"][0]["platformUserId"] == "uid-a"


def test_patch_target_template_rebind(client):
    """换绑模板：合法 id 生效；不存在 404；null 解绑。"""
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    t1 = client.post("/api/templates", json={"name": "T1", "content": "文案一"}).json()
    t2 = client.post("/api/templates", json={"name": "T2", "content": "文案二"}).json()
    target = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": t1["id"]},
    ).json()

    rebound = client.patch(f"/api/targets/{target['id']}", json={"templateId": t2["id"]})
    assert rebound.status_code == 200, rebound.text
    assert rebound.json()["templateId"] == t2["id"]

    unbound = client.patch(f"/api/targets/{target['id']}", json={"templateId": None})
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["templateId"] is None

    ghost = client.patch(f"/api/targets/{target['id']}", json={"templateId": "ghost"})
    assert ghost.status_code == 404
