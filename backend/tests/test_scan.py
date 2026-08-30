"""会话列表扫描（好友批量导入候选）与好友换绑模板的测试。"""

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
        assert payload["titles"] == ["好友A", "好友B", "好友C"]

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
        assert payload["titles"] == []

        updated = client.get("/api/accounts").json()[0]
        assert updated["status"] == "auth_expired"


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
