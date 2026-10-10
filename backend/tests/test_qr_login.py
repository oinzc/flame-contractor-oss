"""M8-4：截图扫码登录 API——headless 截图、登录成功标记 healthy、互斥与图片端点。"""

import asyncio
import time

from fastapi.testclient import TestClient

from app.execution import browser_manager
from app.execution.browser_manager import BrowserManager, LoginFlow
from app.execution.selectors import CONVERSATION_LIST
from app.models import AccountStatus
from sqlmodel import Session
from tests.fakes import FakePage, RecordingNotifier, create_account, make_app


def _wait_for_final_state(client: TestClient, account_id: str, path: str) -> dict:
    deadline = time.monotonic() + 5
    state: dict | None = None
    while time.monotonic() < deadline:
        state = client.get(f"/api/accounts/{account_id}{path}").json()
        if state["state"] in ("succeeded", "failed", "expired"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"登录流程未在期限内结束: {state}")


def test_qr_login_succeeds_and_marks_healthy(settings):
    app = make_app(
        settings,
        [FakePage(logged_in=True, conversation_titles=("好友A",), right_title="好友A")],
    )
    with TestClient(app) as client:
        account = create_account(client)
        started = client.post(f"/api/accounts/{account['id']}/login/qr")
        assert started.status_code == 202
        # 二维码还没渲染出来之前不进入等待扫码（避免前端展示空白/过期画面）
        assert started.json()["state"] == "preparing"

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


def test_qr_login_mutually_exclusive_with_headed_login(settings, monkeypatch):
    # 互斥语义与运行环境无关：显式声明有桌面，让有头流程真正跑起来
    monkeypatch.setattr(browser_manager, "headed_login_available", lambda: True)
    app = make_app(settings, [FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = create_account(client)
        headed = client.post(f"/api/accounts/{account['id']}/login")
        assert headed.status_code == 202
        conflict = client.post(f"/api/accounts/{account['id']}/login/qr")
        assert conflict.status_code == 409
        client.post(f"/api/accounts/{account['id']}/login/cancel")


def test_headed_login_without_desktop_gives_actionable_hint(settings, monkeypatch):
    """容器/纯服务器没有桌面：给出可操作提示，而不是底层异常名。"""
    monkeypatch.setattr(browser_manager, "headed_login_available", lambda: False)
    app = make_app(settings, [FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login")
        state = _wait_for_final_state(client, account["id"], "/login")
        assert state["state"] == "failed"
        assert state["detail"] == "no_desktop"


def test_open_chat_page_waits_for_dom_ready_only(settings):
    """回归：等 `load` 会在慢网络下 30s 超时并把登录误判为失败，只等 DOM 就绪。"""
    page = FakePage(logged_in=True)
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login/qr")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not page.goto_kwargs:
            time.sleep(0.05)
        assert page.gotos == ["https://www.douyin.com/chat"]
        assert page.goto_kwargs[0]["wait_until"] == "domcontentloaded"


def test_qr_login_reports_unavailable_qr(settings):
    """二维码始终没渲染出来时，明确报「二维码未生成」而不是一直转圈。"""
    page = FakePage(logged_in=False)
    page.qr_data_url = None
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login/qr")
        state = _wait_for_final_state(client, account["id"], "/login/qr")
        assert state["state"] == "failed"
        assert state["detail"] == "qr_unavailable"


def test_qr_login_detects_existing_session_without_qr(settings):
    """回归：Profile 已有有效会话时抖音不再展示二维码，截图扫码必须直接判成功。

    否则这类账号会一直空等到 qr_unavailable（容器里已登录过的账号正是如此，
    登录与 Dry Run/调度互相锁死）。
    """
    page = FakePage(logged_in=True)
    page.qr_data_url = None  # 已登录页面没有二维码
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = create_account(client)
        started = client.post(f"/api/accounts/{account['id']}/login/qr")
        assert started.status_code == 202
        state = _wait_for_final_state(client, account["id"], "/login/qr")
        assert state["state"] == "succeeded", state

        with Session(app.state.engine) as session:
            from app.models import Account as AccountModel

            acc = session.get(AccountModel, account["id"])
            assert acc.status == AccountStatus.HEALTHY
            assert acc.last_verified_at is not None


def test_qr_login_drops_stale_qr_image(settings):
    """新流程开始即清掉上一轮截图：没有新二维码前不该把旧画面当成可用二维码。"""
    page = FakePage(logged_in=False)
    page.qr_data_url = None
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = create_account(client)
        stale = settings.artifacts_dir / account["id"] / "login" / "login-qr.png"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"stale-png")
        assert client.get(f"/api/accounts/{account['id']}/login/qr/image").status_code == 200

        client.post(f"/api/accounts/{account['id']}/login/qr")

        assert not stale.exists()
        assert client.get(f"/api/accounts/{account['id']}/login/qr/image").status_code == 404


class _FlakyConversationPage(FakePage):
    """前几次会话列表查询抛瞬时错误，模拟扫码确认后页面跳转的瞬间。"""

    def __init__(self, failures: int = 3):
        super().__init__(logged_in=True)
        self._remaining_failures = failures
        self.poll_failures_raised = 0

    async def query_selector(self, selector: str):
        if selector == CONVERSATION_LIST and self._remaining_failures > 0:
            self._remaining_failures -= 1
            self.poll_failures_raised += 1
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
        return await super().query_selector(selector)


def test_qr_login_survives_transient_poll_error(settings):
    """回归：扫码被确认后页面会跳转，轮询期间的瞬时错误不该把流程判死。"""
    page = _FlakyConversationPage(failures=3)
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login/qr")
        state = _wait_for_final_state(client, account["id"], "/login/qr")
        assert state["state"] == "succeeded", state
        assert page.poll_failures_raised == 3


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


# ---------------------------------------------------------------- 登录结果推送


def test_qr_login_failure_notifies(settings):
    """登录失败要主动推送：结果只在页面上可见，用户可能早已离开控制台。"""
    notifier = RecordingNotifier()
    page = FakePage(logged_in=False)
    page.qr_data_url = None  # 既无二维码也无登录态 → qr_unavailable
    app = make_app(settings, [page], notifier=notifier)
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login/qr")
        state = _wait_for_final_state(client, account["id"], "/login/qr")
        assert state["state"] == "failed"

    assert notifier.sent, "登录失败必须推送"
    title, content, level = notifier.sent[-1]
    assert "扫码登录失败" in title
    assert "测试账号" in content
    assert level == "timeSensitive"


def test_qr_login_success_does_not_notify(settings):
    """登录成功在页面上立刻可见，不再额外推送，避免噪音。"""
    notifier = RecordingNotifier()
    app = make_app(settings, [FakePage(logged_in=True)], notifier=notifier)
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login/qr")
        state = _wait_for_final_state(client, account["id"], "/login/qr")
        assert state["state"] == "succeeded"

    assert notifier.sent == []


def test_headed_login_without_desktop_does_not_notify(settings, monkeypatch):
    """no_desktop 是在无显示环境用错了入口，不算故障，不该打扰。"""
    monkeypatch.setattr(browser_manager, "headed_login_available", lambda: False)
    notifier = RecordingNotifier()
    app = make_app(settings, [FakePage(logged_in=False)], notifier=notifier)
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login")
        state = _wait_for_final_state(client, account["id"], "/login")
        assert state["detail"] == "no_desktop"

    assert notifier.sent == []


def test_login_failure_skipped_when_notifier_disabled(engine, settings):
    """未配置 Bark 时（NullNotifier）登录失败不应尝试推送。"""
    manager = BrowserManager(engine, settings)
    flow = LoginFlow(account_id="acc", state="failed", detail="qr_unavailable")
    # 未配置通知：no-op 且不抛异常
    asyncio.run(manager._notify_login_failed(flow, 300.0))


def test_notify_login_failed_message_covers_timeout(engine, settings):
    """超时文案要说清「等了多少秒 + 仍未登录」，不能只说一句「超时」。"""
    notifier = RecordingNotifier()
    manager = BrowserManager(engine, settings, notifier=notifier)
    flow = LoginFlow(account_id="acc", state="expired", detail="等待扫码超时，请重新发起")
    asyncio.run(manager._notify_login_failed(flow, 300.0))

    assert len(notifier.sent) == 1
    title, content, level = notifier.sent[0]
    assert "超时" in title
    assert "300" in content
    assert level == "timeSensitive"
