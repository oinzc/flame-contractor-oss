"""test_execution 使用共享假浏览器实现。"""

import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from tests.fakes import FakePage, create_account, make_app


def test_migrations_create_expected_tables(engine):
    tables = set(inspect(engine).get_table_names())
    assert {"account", "contract_target", "message_template", "send_job", "send_attempt"} <= tables


def test_login_flow_succeeds_and_marks_healthy(settings):
    app = make_app(settings, [FakePage(become_logged_after=2)])
    with TestClient(app) as client:
        account = create_account(client)

        started = client.post(f"/api/accounts/{account['id']}/login")
        assert started.status_code == 202, started.text
        assert started.json()["state"] == "waiting_scan"

        deadline = time.time() + 5
        state = None
        while time.time() < deadline:
            status = client.get(f"/api/accounts/{account['id']}/login").json()
            state = status["state"]
            if state == "succeeded":
                break
            time.sleep(0.05)
        assert state == "succeeded"

        accounts = client.get("/api/accounts").json()
        updated = next(item for item in accounts if item["id"] == account["id"])
        assert updated["status"] == "healthy"
        assert updated["lastVerifiedAt"] is not None


def test_login_conflict_and_cancel(settings):
    app = make_app(settings, [FakePage(become_logged_after=None)])
    with TestClient(app) as client:
        account = create_account(client)
        url = f"/api/accounts/{account['id']}/login"

        first = client.post(url)
        assert first.status_code == 202

        conflict = client.post(url)
        assert conflict.status_code == 409

        cancelled = client.post(f"{url}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "idle"

        restarted = client.post(url)
        assert restarted.status_code == 202
        client.post(f"{url}/cancel")


def test_dry_run_blocked_while_login_active(settings):
    app = make_app(
        settings,
        [FakePage(become_logged_after=None), FakePage(logged_in=True)],
    )
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login")

        blocked = client.post(f"/api/accounts/{account['id']}/dry-run")
        assert blocked.status_code == 409

        client.post(f"/api/accounts/{account['id']}/login/cancel")


def test_dry_run_locating_targets_without_sending(settings):
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友A", "好友A同学"),
                right_title="好友A",
            )
        ],
    )
    with TestClient(app) as client:
        account = create_account(client)
        for name in ("好友A", "好友A同学", "不存在"):
            response = client.post(
                "/api/targets",
                json={"accountId": account["id"], "nicknameSnapshot": name},
            )
            assert response.status_code == 201, response.text

        result = client.post(f"/api/accounts/{account['id']}/dry-run")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["overall"] == "ok"
        by_name = {item["target"]: item for item in payload["targets"]}
        assert by_name["好友A"]["status"] == "found"
        assert by_name["好友A"]["title"] == "好友A"
        assert by_name["好友A同学"]["status"] == "ambiguous"
        assert by_name["不存在"]["status"] == "not_found"

        assert payload["artifactPath"] is not None
        assert Path(payload["artifactPath"]).is_file()

        updated = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account["id"]
        )
        assert updated["lastVerifiedAt"] is not None
        assert updated["status"] == "pending_login"  # Dry Run 不改变健康状态，只更新时间戳


def test_dry_run_marks_auth_expired(settings):
    app = make_app(settings, [FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = create_account(client)
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
        )

        result = client.post(f"/api/accounts/{account['id']}/dry-run").json()
        assert result["overall"] == "auth_expired"

        updated = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account["id"]
        )
        assert updated["status"] == "auth_expired"


def test_dry_run_finds_friend_via_search(settings):
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友B",),
                right_title="好友A",
                search_results={"好友A": ("好友A",)},
            )
        ],
    )
    with TestClient(app) as client:
        account = create_account(client)
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
        )
        result = client.post(f"/api/accounts/{account['id']}/dry-run").json()
        assert result["targets"][0]["status"] == "found"


def test_dry_run_scroll_fallback_finds_friend(settings):
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友B",),
                right_title="好友A",
                titles_revealed_by_scroll=("好友A",),
            )
        ],
    )
    with TestClient(app) as client:
        account = create_account(client)
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
        )
        result = client.post(f"/api/accounts/{account['id']}/dry-run").json()
        assert result["targets"][0]["status"] == "found"


def test_dry_run_not_found_reports_visible_titles(settings):
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友B", "群聊A(7)"),
                right_title=None,
            )
        ],
    )
    with TestClient(app) as client:
        account = create_account(client)
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
        )
        result = client.post(f"/api/accounts/{account['id']}/dry-run").json()
        assert result["targets"][0]["status"] == "not_found"
        assert result["visibleTitles"] == ["好友B", "群聊A(7)"]
        assert "好友B" in result["targets"][0]["detail"]


def test_dry_run_finds_contact_via_send_message_button(settings):
    """无历史会话的联系人：列表里没有，但搜索能搜到，点「发消息」按钮发起聊天。"""
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友B",),
                right_title="oinzc",
                search_results={"oinzc": ("oinzc",)},
            )
        ],
    )
    with TestClient(app) as client:
        account = create_account(client)
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "oinzc"},
        )
        result = client.post(f"/api/accounts/{account['id']}/dry-run").json()
        assert result["targets"][0]["status"] == "found"
        assert result["visibleTitles"] == ["好友B"]  # 搜索面板里看不到历史列表，符合真实页面


def test_search_requires_warmup_first(settings):
    """真实页面行为：刚打开时搜索索引为空，必须先滚动列表预热才能搜到。"""
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友B",),
                right_title="oinzc",
                search_results={"oinzc": ("oinzc",)},
                search_requires_warmup=True,
            )
        ],
    )
    with TestClient(app) as client:
        account = create_account(client)
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "oinzc"},
        )
        result = client.post(f"/api/accounts/{account['id']}/dry-run").json()
        assert result["targets"][0]["status"] == "found"
