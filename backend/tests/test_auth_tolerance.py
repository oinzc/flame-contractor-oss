"""登录判定容忍：单次抖动不得把账号判死。

背景（2026-10-10 实测）：一次自动扫描的登录判定在 8 秒窗口内误判失效，账号被置为
AUTH_EXPIRED；而调度器会跳过所有非 HEALTHY 账号的扫描与发送，于是账号彻底停摆、
只能靠人工重新扫码恢复——而同一时刻会话其实是有效的（用户点击「截图扫码」后立刻
自行恢复，并未真正扫码）。

因此登录判定失败改为「连续达到容忍次数才标记失效」：单次只计数并告警，账号保持
HEALTHY，下一次判定即可自愈。overall 的取值保持不变（前端与 Schema 依赖该字面量），
未确认与否写在 detail 里，并由调度器结合账号状态决定是否提醒用户重新扫码。
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.execution.browser_manager import BrowserManager
from app.models import Account, AccountStatus
from tests.fakes import FakePage, create_account, make_app


def _set_status(app, account_id: str, status: AccountStatus) -> None:
    """把账号置为指定状态：模拟「登录判定抖动前账号本来是健康的」。"""
    with Session(app.state.engine) as session:
        account = session.get(Account, account_id)
        account.status = status
        session.add(account)
        session.commit()


def _status(app, account_id: str) -> AccountStatus:
    with Session(app.state.engine) as session:
        return session.get(Account, account_id).status


def _scan(client: TestClient, account_id: str) -> dict:
    """同步扫描接口；登录判定失败时返回 overall=auth_expired。"""
    response = client.post(f"/api/accounts/{account_id}/scan-chats")
    assert response.status_code == 200, response.text
    return response.json()


def test_single_auth_failure_does_not_lock_account(settings):
    """一次判定失败：账号仍 HEALTHY，只回报「未确认失效」。"""
    app = make_app(settings, [FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = create_account(client)
        _set_status(app, account["id"], AccountStatus.HEALTHY)

        result = _scan(client, account["id"])

        assert result["overall"] == "auth_expired"  # 契约字面量不变
        assert "未确认" in result["detail"]
        assert _status(app, account["id"]) == AccountStatus.HEALTHY


def test_consecutive_auth_failures_mark_expired(settings):
    """连续两次失败才标记失效：既挡住抖动，也保证真失效仍会被判定。"""
    app = make_app(
        settings, [FakePage(logged_in=False), FakePage(logged_in=False)]
    )
    with TestClient(app) as client:
        account = create_account(client)
        _set_status(app, account["id"], AccountStatus.HEALTHY)

        first = _scan(client, account["id"])
        assert "未确认" in first["detail"]
        assert _status(app, account["id"]) == AccountStatus.HEALTHY

        second = _scan(client, account["id"])
        assert second["detail"] == "登录已失效，请重新扫码"
        assert _status(app, account["id"]) == AccountStatus.AUTH_EXPIRED


def test_successful_check_resets_failure_counter(settings):
    """判定成功即清零：偶发抖动不会跨天累积成一次「失效」。"""
    app = make_app(
        settings,
        [
            FakePage(logged_in=False),  # 第 1 次失败（计数 1，未标记）
            FakePage(logged_in=True),  # 中间成功 → 清零
            FakePage(logged_in=False),  # 再失败：重新从 1 开始，仍不该标记
        ],
    )
    with TestClient(app) as client:
        account = create_account(client)
        _set_status(app, account["id"], AccountStatus.HEALTHY)

        _scan(client, account["id"])
        assert _status(app, account["id"]) == AccountStatus.HEALTHY

        ok = _scan(client, account["id"])
        assert ok["overall"] == "ok"

        _scan(client, account["id"])
        assert _status(app, account["id"]) == AccountStatus.HEALTHY


def test_relogin_clears_failure_counter(settings):
    """重新扫码登录成功（_mark_healthy）同样清零，避免旧计数在下一次抖动时叠加。"""
    app = make_app(
        settings,
        [FakePage(logged_in=False), FakePage(logged_in=True)],
    )
    with TestClient(app) as client:
        account = create_account(client)
        _set_status(app, account["id"], AccountStatus.HEALTHY)

        _scan(client, account["id"])  # 计数 1

        # 截图扫码登录成功：进程内计数应被清掉（页面已登录，无需真正扫码）
        client.post(f"/api/accounts/{account['id']}/login/qr")
        assert _status(app, account["id"]) == AccountStatus.HEALTHY

        manager: BrowserManager = app.state.browser_manager
        assert manager._auth_check_failures.get(account["id"]) is None

        _scan(client, account["id"])  # 若未清零，这里会达到容忍次数而被标记
        assert _status(app, account["id"]) == AccountStatus.HEALTHY


def test_tolerance_is_configurable(engine, settings):
    """容忍次数可调：设为 3 时前两次失败都不标记。"""
    manager = BrowserManager(engine, settings, auth_check_tolerance=3)
    with Session(engine) as session:
        session.add(Account(display_name="容忍测试"))
        session.commit()
        account = session.exec(select(Account)).first()

    assert manager._mark_auth_expired(account.id) is False
    assert manager._mark_auth_expired(account.id) is False
    assert manager._mark_auth_expired(account.id) is True

    with Session(engine) as session:
        assert session.get(Account, account.id).status == AccountStatus.AUTH_EXPIRED


# ------------------------------------------------- 页面未加载 ≠ 登录失效（#2 归类）


def test_load_failure_page_is_not_reported_as_logout(settings):
    """Chromium 错误页导致「找不到会话列表」时，应归类为网络问题而非登录失效。

    否则一次网络抖动会以「请重新扫码」的面目出现（并把账号标成失效、停掉调度），
    而用户照着去扫码也解决不了问题。
    """
    page = FakePage(logged_in=False)
    page.url = "chrome-error://chromewebdata/"  # 完全没加载出抖音页面
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = create_account(client)
        _set_status(app, account["id"], AccountStatus.HEALTHY)

        result = _scan(client, account["id"])

        assert result["overall"] == "error"  # 不是 auth_expired
        assert "网络" in result["detail"]
        assert _status(app, account["id"]) == AccountStatus.HEALTHY

        # 关键：这次失败不该计入「连续判定失败」——否则网络抖动会累积成一次假失效
        manager: BrowserManager = app.state.browser_manager
        assert manager._auth_check_failures.get(account["id"]) is None


def test_douyin_page_without_session_still_counts_as_auth_failure(settings):
    """反面：页面确实在抖音域内、只是没有会话列表 → 仍按登录判定失败处理。

    不能为了躲网络误报而把真失效也放过：那样账号永远不被标记、静默失败。
    """
    page = FakePage(logged_in=False)
    page.url = "https://www.douyin.com/chat"
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = create_account(client)
        _set_status(app, account["id"], AccountStatus.HEALTHY)

        result = _scan(client, account["id"])

        assert result["overall"] == "auth_expired"
        manager: BrowserManager = app.state.browser_manager
        assert manager._auth_check_failures.get(account["id"]) == 1


def test_login_failure_captures_diagnostic_screenshot(settings):
    """判定失败要留现场（截图 + 页面 URL）：没有它只能猜是「慢」「验证页」还是真登出。"""
    page = FakePage(logged_in=False)
    page.url = "https://www.douyin.com/chat"
    app = make_app(settings, [page])
    with TestClient(app) as client:
        account = create_account(client)
        _set_status(app, account["id"], AccountStatus.HEALTHY)

        _scan(client, account["id"])

        shots = [path for path in page.screenshots if path]
        assert shots, "登录判定失败必须截图留证"
        assert "auth_check_failed" in shots[-1]
        assert Path(shots[-1]).exists()

        manager: BrowserManager = app.state.browser_manager
        evidence = manager._auth_evidence.get(account["id"])
        assert evidence is not None
        assert evidence["page_url"] == "https://www.douyin.com/chat"
        assert evidence["screenshot"] == shots[-1]


def test_login_check_timeout_is_configurable(engine, settings):
    """判定预算可配：慢网络下用户可自行放宽，不必等发版。"""
    manager = BrowserManager(engine, settings, login_check_timeout=45.0)
    assert manager._login_check_timeout == 45.0
    assert BrowserManager(engine, settings)._login_check_timeout >= 20.0
