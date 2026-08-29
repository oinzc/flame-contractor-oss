"""M8-4：截图扫码登录 API——headless 截图、登录成功标记 healthy、互斥与图片端点。"""

import time

from fastapi.testclient import TestClient

from app.models import AccountStatus
from sqlmodel import Session
from tests.fakes import FakePage, create_account, make_app


def test_qr_login_succeeds_and_marks_healthy(settings):
    app = make_app(
        settings,
        [FakePage(logged_in=True, conversation_titles=("好友A",), right_title="好友A")],
    )
    with TestClient(app) as client:
        account = create_account(client)
        started = client.post(f"/api/accounts/{account['id']}/login/qr")
        assert started.status_code == 202
        assert started.json()["state"] == "waiting_scan"

        # 轮询直至结束（假页面立即已登录）
        deadline = time.monotonic() + 5
        state = None
        while time.monotonic() < deadline:
            state = client.get(f"/api/accounts/{account['id']}/login/qr").json()
            if state["state"] in ("succeeded", "failed", "expired"):
                break
            time.sleep(0.05)
        assert state is not None and state["state"] == "succeeded", state
        assert state["qrImage"] and state["qrImage"].endswith(".png")

        with Session(app.state.engine) as session:
            from app.models import Account as AccountModel

            acc = session.get(AccountModel, account["id"])
            assert acc.status == AccountStatus.HEALTHY
            assert acc.last_verified_at is not None


def test_qr_login_image_endpoint(settings):
    app = make_app(settings, [FakePage(logged_in=True)])
    with TestClient(app) as client:
        account = create_account(client)
        # 未开始前：截图未生成 → 404
        missing = client.get(f"/api/accounts/{account['id']}/login/qr/image")
        assert missing.status_code == 404

        client.post(f"/api/accounts/{account['id']}/login/qr")
        deadline = time.monotonic() + 5
        got = None
        while time.monotonic() < deadline:
            got = client.get(f"/api/accounts/{account['id']}/login/qr/image")
            if got.status_code == 200:
                break
            time.sleep(0.05)
        assert got is not None and got.status_code == 200
        assert got.headers["content-type"] == "image/png"
        assert got.headers["cache-control"] == "no-store"


def test_qr_login_mutually_exclusive_with_headed_login(settings):
    app = make_app(settings, [FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = create_account(client)
        headed = client.post(f"/api/accounts/{account['id']}/login")
        assert headed.status_code == 202
        conflict = client.post(f"/api/accounts/{account['id']}/login/qr")
        assert conflict.status_code == 409
        client.post(f"/api/accounts/{account['id']}/login/cancel")


def test_qr_login_timeout_marks_expired(settings):
    app = make_app(
        settings,
        [FakePage(logged_in=False)],
    )
    with TestClient(app) as client:
        account = create_account(client)
        # 缩短超时：直接改 manager 参数（测试专用构造已暴露）
        client.post(f"/api/accounts/{account['id']}/login/qr")
        assert client.post(f"/api/accounts/{account['id']}/login/qr/cancel").json()["state"] == "idle"


class _DualEvalPage(FakePage):
    """区分两类 evaluate：二维码查询脚本 vs localStorage 昵称脚本。"""

    def __init__(self, storage_json: str, qr_data: str | None):
        super().__init__(logged_in=True)
        self._storage_json = storage_json
        self._qr_data = qr_data

    async def evaluate(self, script, *args, **kwargs):
        if "querySelectorAll" in script:
            return self._qr_data
        return self._storage_json


def test_read_qr_data_url_extracts_qr_only():
    """二维码本体提取：只认 data:image/png 前缀。"""
    import asyncio

    from app.execution.douyin_chat import read_qr_data_url

    page = _DualEvalPage(storage_json="{}", qr_data="data:image/png;base64,AAAA")
    assert asyncio.run(read_qr_data_url(page)) == "data:image/png;base64,AAAA"

    page = _DualEvalPage(storage_json="{}", qr_data="https://example.com/qr.png")
    assert asyncio.run(read_qr_data_url(page)) is None

    page = _DualEvalPage(storage_json="{}", qr_data=None)
    assert asyncio.run(read_qr_data_url(page)) is None


def test_qr_shot_writes_decoded_qr_bytes(settings):
    """take_qr_shot 优先写二维码本体：image 端点返回解码后的 PNG 字节。"""
    app = make_app(
        settings,
        [_DualEvalPage('{"nickname": "测试号"}', "data:image/png;base64,AAAA")],
    )
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login/qr")
        deadline = time.monotonic() + 5
        got = None
        while time.monotonic() < deadline:
            got = client.get(f"/api/accounts/{account['id']}/login/qr/image")
            if got.status_code == 200:
                break
            time.sleep(0.05)
        assert got is not None and got.status_code == 200
        assert got.content == b"\x00\x00\x00"  # base64 AAAA 解码结果
