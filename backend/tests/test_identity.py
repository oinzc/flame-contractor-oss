"""好友稳定身份（抖音 sec_uid）采集、核对与批量导入测试。"""

import asyncio

from tests.fakes import FakePage, make_app
from app.execution.douyin_chat import DouyinChat


class _IdentityResponse:
    def __init__(self, payload: dict):
        self.url = "https://www.douyin.com/aweme/v1/web/im/user/info/"
        self._payload = payload

    async def json(self):
        return self._payload


class _EventPage(FakePage):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._handlers: dict[str, list] = {}

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    async def emit_response(self, response: _IdentityResponse) -> None:
        for handler in self._handlers.get("response", []):
            handler(response)
        await asyncio.sleep(0)


def test_scan_collects_sec_uid_from_user_info_response():
    async def scenario():
        page = _EventPage(logged_in=True, conversation_titles=("好友展示名",))
        chat = DouyinChat(page, sleep=lambda _: asyncio.sleep(0))
        await page.emit_response(
            _IdentityResponse(
                {
                    "data": [
                        {
                            "nickname": "平台昵称",
                            "remark_name": "好友展示名",
                            "sec_uid": "sec-user-1",
                        }
                    ]
                }
            )
        )

        entries = await chat.scan_all_chats()

        assert entries == [
            {
                "title": "好友展示名",
                "preview": "",
                "streak_days": None,
                "streak_kind": None,
                "streak_label": None,
                "platform_user_id": "sec-user-1",
            }
        ]

    asyncio.run(scenario())


def test_same_label_with_two_platform_ids_is_not_auto_bound():
    async def scenario():
        page = _EventPage(logged_in=True, conversation_titles=("同名好友",))
        chat = DouyinChat(page, sleep=lambda _: asyncio.sleep(0))
        await page.emit_response(
            _IdentityResponse(
                {
                    "data": [
                        {"nickname": "甲", "remark_name": "同名好友", "sec_uid": "sec-1"},
                        {"nickname": "乙", "remark_name": "同名好友", "sec_uid": "sec-2"},
                    ]
                }
            )
        )

        entries = await chat.scan_all_chats()

        assert entries[0]["platform_user_id"] is None

    asyncio.run(scenario())


def test_open_conversation_requires_matching_platform_id():
    async def scenario():
        page = _EventPage(
            logged_in=True,
            conversation_titles=("好友",),
            right_title="好友",
        )
        chat = DouyinChat(page, sleep=lambda _: asyncio.sleep(0), confirm_timeout=0.1)
        await page.emit_response(
            _IdentityResponse(
                {"data": [{"nickname": "好友", "sec_uid": "actual-user"}]}
            )
        )

        opened = await chat.open_conversation("好友", "expected-user")

        assert opened.opened is True
        assert opened.exact is False
        assert opened.identity_verified is False
        assert opened.platform_user_id == "actual-user"

    asyncio.run(scenario())


def test_batch_import_backfills_and_renames_by_platform_id(client):
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    original = client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "旧昵称",
            "platformUserId": "sec-user-1",
        },
    )
    assert original.status_code == 201, original.text

    renamed = client.post(
        "/api/targets/batch",
        json={
            "accountId": account["id"],
            "items": [{"nickname": "新昵称", "platformUserId": "sec-user-1"}],
        },
    )
    assert renamed.status_code == 201, renamed.text
    assert renamed.json()["refreshed"] == ["新昵称"]

    targets = client.get("/api/targets", params={"accountId": account["id"]}).json()
    assert len(targets) == 1
    assert targets[0]["nicknameSnapshot"] == "新昵称"
    assert targets[0]["platformUserId"] == "sec-user-1"


def test_batch_import_backfills_legacy_target_and_rejects_identity_conflict(client):
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    legacy = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "老好友"},
    ).json()

    backfilled = client.post(
        "/api/targets/batch",
        json={
            "accountId": account["id"],
            "items": [{"nickname": "老好友", "platformUserId": "sec-user-2"}],
        },
    )
    assert backfilled.status_code == 201, backfilled.text
    assert backfilled.json()["refreshed"] == ["老好友"]

    conflict = client.post(
        "/api/targets/batch",
        json={
            "accountId": account["id"],
            "items": [{"nickname": "老好友", "platformUserId": "sec-user-3"}],
        },
    )
    assert conflict.status_code == 201, conflict.text
    assert conflict.json()["failed"] == ["老好友：稳定身份与已有好友不一致"]

    targets = client.get("/api/targets", params={"accountId": account["id"]}).json()
    assert targets[0]["id"] == legacy["id"]
    assert targets[0]["platformUserId"] == "sec-user-2"
