"""登录判定容忍：单次抖动不得把账号判死。

背景（2026-10-10 实测）：一次自动扫描的登录判定在 8 秒窗口内误判失效，账号被置为
AUTH_EXPIRED；而调度器会跳过所有非 HEALTHY 账号的扫描与发送，于是账号彻底停摆、
只能靠人工重新扫码恢复——而同一时刻会话其实是有效的（用户点击「截图扫码」后立刻
自行恢复，并未真正扫码）。

因此登录判定失败改为「连续达到容忍次数才标记失效」：单次只计数并告警，账号保持
HEALTHY，下一次判定即可自愈。overall 的取值保持不变（前端与 Schema 依赖该字面量），
未确认与否写在 detail 里，并由调度器结合账号状态决定是否提醒用户重新扫码。
"""

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
