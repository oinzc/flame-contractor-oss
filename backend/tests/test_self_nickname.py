"""登录成功后读取当前抖音身份昵称（localStorage user_info，仅展示用途）。"""

import asyncio
import json

from fastapi.testclient import TestClient

from app.execution.douyin_chat import DouyinChat, read_self_nickname
from tests.fakes import FakePage, create_account, make_app


def _page_with_storage(nickname: str | None) -> FakePage:
    page = FakePage(logged_in=True)
    if nickname is None:
        page.evaluate_result = None
    else:
        page.evaluate_result = json.dumps({"nickname": nickname, "uid": "<略>"})
    return page


def test_read_self_nickname_extracts_nickname():
    page = _page_with_storage("测试号")
    chat = DouyinChat(page)
    assert asyncio.run(read_self_nickname(page)) == "测试号"


def test_read_self_nickname_missing_or_broken_returns_none():
    chat = DouyinChat(_page_with_storage(None))
    assert asyncio.run(read_self_nickname(chat.page)) is None

    broken = _page_with_storage("x")
    broken.evaluate_result = "not-json{"
    assert asyncio.run(read_self_nickname(broken)) is None

    empty = _page_with_storage("x")
    empty.evaluate_result = json.dumps({"uid": "<略>"})
    assert asyncio.run(read_self_nickname(empty)) is None


def test_qr_login_status_reports_self_nickname(settings):
    app = make_app(settings, [_page_with_storage("测试号")])
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login/qr")
        import time

        deadline = time.monotonic() + 5
        state = {}
        while time.monotonic() < deadline:
            state = client.get(f"/api/accounts/{account['id']}/login/qr").json()
            if state["state"] in ("succeeded", "failed", "expired"):
                break
            time.sleep(0.05)
        assert state["state"] == "succeeded"
        assert state["selfNickname"] == "测试号"
