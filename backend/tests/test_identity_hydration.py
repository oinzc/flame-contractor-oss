"""已存在普通好友的稳定身份补采：精确打开、回填与 SQLite 进度。"""

import time

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import build_engine
from app.execution.browser_manager import BrowserManager
from app.models import ContractTarget
from tests.fakes import FakePage, make_app


def _create_account_and_target(client: TestClient, nickname: str = "好友A") -> tuple[dict, dict]:
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    target = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": nickname},
    ).json()
    return account, target


def _wait_hydration(client: TestClient, account_id: str, task_id: str) -> dict:
    deadline = time.monotonic() + 5
    snapshot = client.get(
        f"/api/accounts/{account_id}/identity-hydration/progress",
        params={"taskId": task_id},
    ).json()
    while snapshot["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = client.get(
            f"/api/accounts/{account_id}/identity-hydration/progress",
            params={"taskId": task_id},
        ).json()
    return snapshot


def test_identity_hydration_opens_exact_chat_and_backfills_target(settings):
    page = FakePage(
        logged_in=True,
        conversation_titles=("好友A",),
        right_title="好友A",
        identity_payloads={
            "好友A": {
                "data": [
                    {
                        "nickname": "平台昵称A",
                        "remark_name": "好友A",
                        "unique_id": "friend_a",
                        "sec_uid": "sec-user-a",
                    }
                ]
            }
        },
    )
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account, target = _create_account_and_target(client)
        started = client.post(
            f"/api/accounts/{account['id']}/identity-hydration/start",
            json={"targetIds": [target["id"]]},
        )
        assert started.status_code == 202, started.text

        finished = _wait_hydration(client, account["id"], started.json()["taskId"])
        assert finished["status"] == "completed", finished
        assert finished["overall"] == "ok"
        assert finished["completed"] == finished["total"] == 1
        assert finished["results"][0]["status"] == "bound"
        assert finished["results"][0]["platformUserId"] == "sec-user-a"
        assert finished["results"][0]["platformHandle"] == "friend_a"
        assert page.clicked == [("好友A", "chat_btn")]

        stored = client.get("/api/targets", params={"accountId": account["id"]})
        assert stored.status_code == 200, stored.text
        stored_target = stored.json()[0]
        assert stored_target["platformUserId"] == "sec-user-a"
        assert stored_target["platformHandle"] == "friend_a"
        assert stored_target["conversationType"] == "user"

        latest = client.get(f"/api/accounts/{account['id']}/identity-hydration/latest")
        assert latest.status_code == 200, latest.text
        assert latest.json()["taskId"] == started.json()["taskId"]


def test_identity_hydration_does_not_bind_without_new_identity_response(settings):
    page = FakePage(
        logged_in=True,
        conversation_titles=("好友A",),
        right_title="好友A",
    )
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account, target = _create_account_and_target(client)
        started = client.post(
            f"/api/accounts/{account['id']}/identity-hydration/start",
            json={"targetIds": [target["id"]]},
        )
        finished = _wait_hydration(client, account["id"], started.json()["taskId"])

        assert finished["status"] == "completed", finished
        assert finished["overall"] == "partial"
        result = finished["results"][0]
        assert result["status"] == "not_observed"
        assert result["opened"] is True
        assert "本次用户身份响应" in result["detail"]

        with Session(app.state.engine) as session:
            stored = session.get(ContractTarget, target["id"])
            assert stored is not None
            assert stored.platform_user_id is None


def test_identity_hydration_skips_group_targets(settings):
    app = make_app(settings, [FakePage(logged_in=True)])
    with TestClient(app) as client:
        account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
        target = client.post(
            "/api/targets",
            json={
                "accountId": account["id"],
                "nicknameSnapshot": "群聊",
                "platformConversationId": "6991386785649590814",
                "conversationType": "group",
            },
        ).json()
        response = client.post(
            f"/api/accounts/{account['id']}/identity-hydration/start",
            json={"targetIds": [target["id"]]},
        )
        assert response.status_code == 409, response.text
        assert "普通好友" in response.json()["detail"]


def test_identity_hydration_progress_survives_manager_recreation(settings):
    page = FakePage(
        logged_in=True,
        conversation_titles=("好友A",),
        right_title="好友A",
        identity_payloads={"好友A": {"data": [{"nickname": "好友A", "sec_uid": "sec-a"}]}},
    )
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account, target = _create_account_and_target(client)
        started = client.post(
            f"/api/accounts/{account['id']}/identity-hydration/start",
            json={"targetIds": [target["id"]]},
        ).json()
        finished = _wait_hydration(client, account["id"], started["taskId"])
        assert finished["status"] == "completed"

        reader = BrowserManager(build_engine(settings.database_url), settings)
        try:
            restored = reader.identity_hydration_progress(account["id"], started["taskId"])
        finally:
            reader._engine.dispose()

        assert restored["status"] == "completed"
        assert restored["results"][0]["platform_user_id"] == "sec-a"
