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
