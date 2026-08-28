"""headless 登录检测假阴性修复：wait_logged_in 轮询等待会话列表出现。

背景：run_send/run_dry_run 以前用 is_logged_in() 单次查询，页面加载早期会话列表
未渲染完即返回 False，被误判 auth_expired 并跳过发送。现改为 wait_logged_in 轮询，
会话列表一旦出现即判已登录，超时才判未登录。FakePage 用 become_logged_after
模拟「会话列表延迟渲染」。
"""

import asyncio

from fastapi.testclient import TestClient

from app.execution.douyin_chat import DouyinChat
from tests.fakes import FakePage, create_account, make_app


def _chat(page: FakePage) -> DouyinChat:
    return DouyinChat(page, sleep=lambda _s: asyncio.sleep(0))


def test_wait_logged_in_polls_until_list_renders():
    # 会话列表第 3 次轮询才出现 → 应判已登录
    page = FakePage(logged_in=False, become_logged_after=3)
    chat = _chat(page)
    assert asyncio.run(chat.wait_logged_in(timeout=5.0)) is True


def test_wait_logged_in_times_out_when_never_renders():
    # 会话列表始终不出现 → 超时判未登录
    page = FakePage(logged_in=False, become_logged_after=float('inf'))
    chat = _chat(page)
    assert asyncio.run(chat.wait_logged_in(timeout=0.2)) is False


def _account_with_text_target(client: TestClient) -> dict:
    account = create_account(client)
    tmpl = client.post(
        "/api/templates",
        json={"name": "T", "content": "{{target_name}}，第 {{streak_days}} 天。"},
    ).json()
    client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": tmpl["id"]},
    )
    return account


def test_run_send_recovers_when_list_renders_late(settings):
    """登录列表延迟渲染（几次轮询后出现）→ 不再误判 auth_expired，应发送成功。"""
    page = FakePage(
        logged_in=False,
        become_logged_after=3,
        conversation_titles=("好友A",),
        right_title="好友A",
    )
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = _account_with_text_target(client)
        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["overall"] == "ok", result
        assert result["results"][0]["status"] == "succeeded", result


def test_run_send_still_auth_expired_when_never_renders(settings):
    """会话列表始终不出现 → 仍正确判 auth_expired（超时而非误判）。"""
    page = FakePage(
        logged_in=False,
        become_logged_after=float('inf'),
        conversation_titles=("好友A",),
        right_title="好友A",
    )
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = _account_with_text_target(client)
        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["overall"] == "auth_expired", result
