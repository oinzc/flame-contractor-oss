"""M5：抖音原生表情（sticker）发送。

send_sticker 参考 unmev/douyin-auto-fire 的 send_douyin_sticker()：
点表情按钮 → 打开面板 → 按名称匹配表情项 → 一点即发 → 「从我的 + 非头像」增图确认。
终态边界与图片一致：宁可不发不错发，未确认到真实气泡绝不判成功。
"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import JobStatus, SendJob
from tests.fakes import FakePage, create_account, make_app


def _sticker_page(*, sticker_items=("比心",), image_bubble_render: bool = True) -> FakePage:
    return FakePage(
        logged_in=True,
        conversation_titles=("好友A",),
        right_title="好友A",
        image_bubble_render=image_bubble_render,
        sticker_items=list(sticker_items),
    )


def _create_sticker_send(
    client: TestClient,
    *,
    name: str,
    category: str | None = None,
):
    account = create_account(client)
    payload = {"name": "Stk", "type": "sticker", "stickerName": name}
    if category:
        payload["stickerCategory"] = category
    tmpl = client.post("/api/templates", json=payload).json()
    client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": tmpl["id"]},
    )
    return account


# ---------------------------------------------------------------- 模板校验


def test_create_sticker_template_validates(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        assert (
            client.post("/api/templates", json={"name": "S", "type": "sticker"}).status_code
            == 422
        )

        ok = client.post(
            "/api/templates",
            json={"name": "S", "type": "sticker", "stickerName": "比心", "stickerCategory": "常用"},
        )
        assert ok.status_code == 201, ok.text
        body = ok.json()
        assert body["type"] == "sticker"
        assert body["stickerName"] == "比心"
        assert body["stickerCategory"] == "常用"


# ---------------------------------------------------------------- 发送


def test_send_sticker_matches_and_succeeds(settings):
    page = _sticker_page(sticker_items=("比心", "哭"))
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = _create_sticker_send(client, name="比心")
        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["results"][0]["status"] == "succeeded", result
        assert page.sticker_clicked == "比心"

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status == JobStatus.SUCCEEDED
        assert client.get("/api/targets").json()[0]["streakDays"] == 1


def test_send_sticker_missing_face_fails(settings):
    """面板里没有该表情 → 找不到 → failed；绝不误判成功。"""
    page = _sticker_page(sticker_items=("哭",))
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = _create_sticker_send(client, name="比心")
        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["results"][0]["status"] == "failed_retryable", result
        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status != JobStatus.SUCCEEDED


def test_send_sticker_no_bubble_not_success(settings):
    """表情点了但「从我的 + 非头像」气泡没出现 → 不误判成功。"""
    page = _sticker_page(sticker_items=("比心",), image_bubble_render=False)
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = _create_sticker_send(client, name="比心")
        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["results"][0]["status"] != "succeeded", result
        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status != JobStatus.SUCCEEDED
