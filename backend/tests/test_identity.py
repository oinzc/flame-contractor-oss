"""好友稳定身份（抖音 sec_uid）采集、核对与批量导入测试。"""

import asyncio

from tests.fakes import FakePage, make_app
from app.execution.douyin_chat import DouyinChat, parse_group_conversations


class _IdentityResponse:
    def __init__(self, payload: dict):
        self.url = "https://www.douyin.com/aweme/v1/web/im/user/info/"
        self._payload = payload

    async def json(self):
        return self._payload


class _ConversationResponse:
    def __init__(self, body: bytes):
        self.url = "https://imapi.douyin.com/v1/message/get_message_by_init"
        self._body = body

    async def body(self):
        return self._body


def _varint(value: int) -> bytes:
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _bytes_field(number: int, value: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _varint_field(number: int, value: int) -> bytes:
    return _varint(number << 3) + _varint(value)


def _group_record(conversation_id: str, title: str, kind: int = 2) -> bytes:
    return b"".join(
        (
            _bytes_field(1, conversation_id.encode("ascii")),
            _varint_field(2, int(conversation_id)),
            _varint_field(3, kind),
            _bytes_field(5, title.encode("utf-8")),
        )
    )


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
                            "unique_id": "friend_handle",
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
                "platform_handle": "friend_handle",
                "identity_status": "bound",
                "identity_source": "remark_name",
                "identity_candidate_count": 1,
                "identity_detail": "通过备注名唯一匹配",
                "platform_conversation_id": None,
                "conversation_type": None,
                "conversation_status": "not_observed",
                "conversation_candidate_count": 0,
                "conversation_detail": "本次扫描未观察到带唯一 ID 的群会话数据，未猜测绑定",
            }
        ]

    asyncio.run(scenario())


def test_parse_group_conversation_protobuf_requires_matching_id_and_group_kind():
    payload = b"".join(
        (
            _bytes_field(50, _group_record("6991386785649590814", "老龙帮")),
            _bytes_field(50, _group_record("7596223999412011578", "保时捷车友会", kind=1)),
            _bytes_field(50, _group_record("7265326568279458360", "一帅比&俩二臂")),
        )
    )

    records = parse_group_conversations(payload)

    assert [(item.platform_conversation_id, item.title) for item in records] == [
        ("6991386785649590814", "老龙帮"),
        ("7265326568279458360", "一帅比&俩二臂"),
    ]


def test_scan_collects_group_conversation_id_and_name():
    async def scenario():
        page = _EventPage(logged_in=True, conversation_titles=("老龙帮",))
        chat = DouyinChat(page, sleep=lambda _: asyncio.sleep(0))
        await page.emit_response(
            _ConversationResponse(
                _bytes_field(50, _group_record("6991386785649590814", "老龙帮"))
            )
        )

        entries = await chat.scan_all_chats()

        assert entries[0]["platform_user_id"] is None
        assert entries[0]["platform_conversation_id"] == "6991386785649590814"
        assert entries[0]["conversation_type"] == "group"
        assert entries[0]["conversation_status"] == "bound"
        assert "protobuf" in entries[0]["conversation_detail"]

    asyncio.run(scenario())


def test_same_group_title_with_two_conversation_ids_is_not_auto_bound():
    async def scenario():
        page = _EventPage(logged_in=True, conversation_titles=("同名群",))
        chat = DouyinChat(page, sleep=lambda _: asyncio.sleep(0))
        await page.emit_response(
            _ConversationResponse(
                b"".join(
                    (
                        _bytes_field(50, _group_record("6991386785649590814", "同名群")),
                        _bytes_field(50, _group_record("7596223999412011578", "同名群")),
                    )
                )
            )
        )

        entries = await chat.scan_all_chats()

        assert entries[0]["platform_conversation_id"] is None
        assert entries[0]["conversation_status"] == "ambiguous"
        assert entries[0]["conversation_candidate_count"] == 2

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
        assert entries[0]["identity_status"] == "ambiguous"
        assert entries[0]["identity_candidate_count"] == 2

    asyncio.run(scenario())


def test_scan_marks_identity_not_observed_without_guessing():
    async def scenario():
        page = _EventPage(logged_in=True, conversation_titles=("未返回身份的好友",))
        chat = DouyinChat(page, sleep=lambda _: asyncio.sleep(0))

        entries = await chat.scan_all_chats()

        assert entries[0]["platform_user_id"] is None
        assert entries[0]["identity_status"] == "not_observed"
        assert "未观察到" in entries[0]["identity_detail"]

    asyncio.run(scenario())


def test_search_title_ignores_unread_count_suffix():
    class SearchItem:
        async def query_selector(self, selector):
            if "search_highlight" in selector:
                return _TextElement("老龙帮")
            if "SearchPanelitemtitle" in selector:
                return _TextElement("老龙帮\n(9)")
            return None

        async def inner_text(self):
            return "老龙帮\n(9)\n861\n发消息"

    class _TextElement:
        def __init__(self, text):
            self.text = text

        async def inner_text(self):
            return self.text

    async def scenario():
        chat = DouyinChat(_EventPage(), sleep=lambda _: asyncio.sleep(0))
        assert await chat._title_of(SearchItem()) == "老龙帮"

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


def test_open_conversation_requires_matching_group_conversation_id():
    async def scenario():
        page = _EventPage(
            logged_in=True,
            conversation_titles=("老龙帮",),
            right_title="老龙帮",
        )
        chat = DouyinChat(page, sleep=lambda _: asyncio.sleep(0), confirm_timeout=0.1)
        await page.emit_response(
            _ConversationResponse(
                _bytes_field(50, _group_record("6991386785649590814", "老龙帮"))
            )
        )

        opened = await chat.open_conversation(
            "老龙帮",
            platform_conversation_id="6991386785649590814",
        )

        assert opened.opened is True
        assert opened.exact is True
        assert opened.identity_verified is True
        assert opened.platform_conversation_id == "6991386785649590814"
        assert opened.conversation_type == "group"

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


def test_batch_import_persists_readable_handle_and_reports_identity_state(client):
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()

    imported = client.post(
        "/api/targets/batch",
        json={
            "accountId": account["id"],
            "items": [
                {
                    "nickname": "已识别好友",
                    "platformUserId": "sec-user-1",
                    "platformHandle": "friend_handle",
                    "identityStatus": "bound",
                },
                {
                    "nickname": "未识别好友",
                    "identityStatus": "not_observed",
                },
                {
                    "nickname": "同名好友",
                    "identityStatus": "ambiguous",
                },
            ],
        },
    )

    assert imported.status_code == 201, imported.text
    payload = imported.json()
    assert payload["identityBound"] == ["已识别好友"]
    assert payload["identityUnresolved"] == ["未识别好友"]
    assert payload["identityAmbiguous"] == ["同名好友"]

    targets = client.get("/api/targets", params={"accountId": account["id"]}).json()
    readable = next(target for target in targets if target["nicknameSnapshot"] == "已识别好友")
    assert readable["platformHandle"] == "friend_handle"


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


def test_batch_import_persists_and_renames_by_group_conversation_id(client):
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    original = client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "旧群名",
            "platformConversationId": "6991386785649590814",
            "conversationType": "group",
        },
    )
    assert original.status_code == 201, original.text

    renamed = client.post(
        "/api/targets/batch",
        json={
            "accountId": account["id"],
            "items": [
                {
                    "nickname": "新群名",
                    "platformConversationId": "6991386785649590814",
                    "conversationType": "group",
                    "conversationStatus": "bound",
                }
            ],
        },
    )
    assert renamed.status_code == 201, renamed.text
    assert renamed.json()["refreshed"] == ["新群名"]
    assert renamed.json()["conversationBound"] == ["新群名"]

    targets = client.get("/api/targets", params={"accountId": account["id"]}).json()
    assert len(targets) == 1
    assert targets[0]["nicknameSnapshot"] == "新群名"
    assert targets[0]["platformConversationId"] == "6991386785649590814"
    assert targets[0]["conversationType"] == "group"


def test_target_identity_input_rejects_mixed_user_and_group_ids(client):
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()

    response = client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "身份类型冲突",
            "platformUserId": "sec-user-1",
            "platformConversationId": "6991386785649590814",
        },
    )

    assert response.status_code == 422
    assert "不能同时填写" in response.text


def test_conversation_id_defaults_to_group_identity_type(client):
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()

    response = client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "自动识别群聊",
            "platformConversationId": "6991386785649590814",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["conversationType"] == "group"
