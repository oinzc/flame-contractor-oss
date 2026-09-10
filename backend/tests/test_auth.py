"""平台账号(User)认证与多租户数据隔离测试。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import create_app
from app.models import Account, MessageTemplate, User
from conftest import TEST_ADMIN_PASSWORD


def _create_user(client: TestClient, username: str, password: str = "pw123456") -> dict:
    resp = client.post("/api/auth/users", json={"username": username, "password": password})
    assert resp.status_code == 201, resp.text
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_first_run_requires_one_time_admin_setup(settings):
    app = create_app(settings)
    app.state.disable_test_auto_auth = True
    with TestClient(app) as test_client:
        assert test_client.get("/api/auth/setup").json() == {"needsSetup": True}
        assert test_client.post(
            "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        ).status_code == 401

        created = test_client.post(
            "/api/auth/register",
            json={"username": "owner", "password": "owner-pass-123", "displayName": "所有者"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["user"]["isSuperuser"] is True
        assert test_client.get("/api/auth/setup").json() == {"needsSetup": False}
        assert test_client.post(
            "/api/auth/register", json={"username": "second", "password": "second-pass"}
        ).status_code == 409


def test_register_login_me_roundtrip(client):
    auth = _create_user(client, "carol", "abc12345")
    token = auth["token"]
    head = _auth_headers(token)

    me = client.get("/api/auth/me", headers=head)
    assert me.status_code == 200
    assert me.json()["username"] == "carol"

    login = client.post("/api/auth/login", json={"username": "carol", "password": "abc12345"})
    assert login.status_code == 200
    assert login.json()["token"]


def test_login_wrong_password_401(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_register_duplicate_username_409(client):
    _create_user(client, "dup")
    resp = client.post("/api/auth/users", json={"username": "dup", "password": "another-pass"})
    assert resp.status_code == 409


def test_new_and_changed_passwords_require_eight_characters(client):
    assert client.post(
        "/api/auth/users", json={"username": "weak", "password": "short"}
    ).status_code == 422
    assert client.post(
        "/api/auth/me/password",
        json={"currentPassword": TEST_ADMIN_PASSWORD, "newPassword": "short"},
    ).status_code == 422


def test_no_token_401_on_private_endpoints(client):
    # client fixture 自动注入 admin token；这里移除它来模拟未登录请求
    client.headers.pop("Authorization", None)
    resp = client.get("/api/accounts")
    assert resp.status_code == 401


def test_invalid_token_401(client):
    resp = client.get("/api/accounts", headers=_auth_headers("not-a-real-token"))
    assert resp.status_code == 401


def test_accounts_isolated_between_users(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        alice = _create_user(client, "alice")
        bob = _create_user(client, "bob")

        # alice 建账号
        created = client.post(
            "/api/accounts",
            json={"displayName": "alice-account"},
            headers=_auth_headers(alice["token"]),
        )
        assert created.status_code == 201, created.text

        # 各自看到的账号不同
        alice_accs = client.get("/api/accounts", headers=_auth_headers(alice["token"])).json()
        bob_accs = client.get("/api/accounts", headers=_auth_headers(bob["token"])).json()
        assert len(alice_accs) == 1
        assert len(bob_accs) == 0

        # alice 的账号 bob 无法访问/改/删（越权 → 404）
        acc_id = alice_accs[0]["id"]
        assert client.patch(
            f"/api/accounts/{acc_id}",
            json={"displayName": "hack"},
            headers=_auth_headers(bob["token"]),
        ).status_code == 404
        assert client.delete(
            f"/api/accounts/{acc_id}", headers=_auth_headers(bob["token"])
        ).status_code == 404
        # bob 不能建同账号名（账号名唯一性是 user 内，允许 bob 各自建同名）
        bob_dup = client.post(
            "/api/accounts",
            json={"displayName": "alice-account"},
            headers=_auth_headers(bob["token"]),
        )
        assert bob_dup.status_code == 201


def test_templates_isolated_between_users(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        alice = _create_user(client, "alice2")
        bob = _create_user(client, "bob2")

        t = client.post(
            "/api/templates",
            json={"name": "模板A", "content": "续火"},
            headers=_auth_headers(alice["token"]),
        )
        assert t.status_code == 201, t.text

        alice_templates = client.get("/api/templates", headers=_auth_headers(alice["token"])).json()
        bob_templates = client.get("/api/templates", headers=_auth_headers(bob["token"])).json()
        assert len(alice_templates) == 1
        assert len(bob_templates) == 0

        # bob 无法访问 alice 的模板
        tid = alice_templates[0]["id"]
        assert client.patch(
            f"/api/templates/{tid}", json={"name": "hack"}, headers=_auth_headers(bob["token"])
        ).status_code == 404
        assert client.delete(
            f"/api/templates/{tid}", headers=_auth_headers(bob["token"])
        ).status_code == 404

        # bob 可以建同名模板（唯一性改为 user 内）
        assert client.post(
            "/api/templates",
            json={"name": "模板A", "content": "另一条"},
            headers=_auth_headers(bob["token"]),
        ).status_code == 201


def test_execution_endpoint_rejects_other_users_account(settings):
    """execution 嵌套路由（/accounts/{id}/...）也做越权校验。"""
    app = create_app(settings)
    with TestClient(app) as client:
        alice = _create_user(client, "alice3")
        bob = _create_user(client, "bob3")

        created = client.post(
            "/api/accounts",
            json={"displayName": "alice-acc"},
            headers=_auth_headers(alice["token"]),
        ).json()
        acc_id = created["id"]

        # bob 对该账号发起登录前置（越权）
        resp = client.get(f"/api/accounts/{acc_id}/login", headers=_auth_headers(bob["token"]))
        assert resp.status_code == 404


def test_dashboard_scoped_to_user(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        alice = _create_user(client, "alice4")
        bob = _create_user(client, "bob4")

        client.post(
            "/api/accounts",
            json={"displayName": "alice-acc"},
            headers=_auth_headers(alice["token"]),
        )

        alice_dash = client.get("/api/dashboard", headers=_auth_headers(alice["token"])).json()
        bob_dash = client.get("/api/dashboard", headers=_auth_headers(bob["token"])).json()
        assert alice_dash["accountCount"] == 1
        assert bob_dash["accountCount"] == 0
        assert len(alice_dash["accounts"]) == 1
        assert len(bob_dash["accounts"]) == 0


def test_initialized_admin_is_superuser(client):
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
    ).json()
    me = client.get("/api/auth/me", headers=_auth_headers(login["token"]))
    assert me.status_code == 200
    assert me.json()["isSuperuser"] is True


def test_register_supports_profile_fields(client):
    """管理员创建用户可携带 displayName/email；登录后 me 返回这些字段。"""
    resp = client.post(
        "/api/auth/users",
        json={
            "username": "profile1",
            "password": "profile-pass",
            "displayName": "小明",
            "email": "xm@x.com",
        },
    )
    assert resp.status_code == 201
    user = resp.json()
    assert user["displayName"] == "小明"
    assert user["email"] == "xm@x.com"

    login = client.post(
        "/api/auth/login", json={"username": "profile1", "password": "profile-pass"}
    ).json()
    me = client.get("/api/auth/me", headers=_auth_headers(login["token"]))
    assert me.json()["displayName"] == "小明"


def test_update_me_requires_superuser_for_email_collision(client):
    """更新资料：邮箱被他人占用时 409。"""
    client.post(
        "/api/auth/users",
        json={"username": "a1", "password": "a1-password", "email": "a1@x.com"},
    )
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
    ).json()
    # 管理员邮箱暂未占用，改成 a1 的用户邮箱应冲突
    dup = client.patch(
        "/api/auth/me",
        json={"email": "a1@x.com"},
        headers=_auth_headers(login["token"]),
    )
    assert dup.status_code == 409


def test_change_password_revokes_other_sessions(client, settings):
    """改密码：校验旧密码、新旧不同、让其它会话失效（当前会话保留）。"""
    app = create_app(settings)
    with TestClient(app) as client:
        # 登录得到两个不同会话
        login_a = client.post(
            "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        ).json()
        login_b = client.post(
            "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        ).json()

        # 用 A 会话改密码，A 保留，B 应被撤销
        resp = client.post(
            "/api/auth/me/password",
            json={"currentPassword": TEST_ADMIN_PASSWORD, "newPassword": "new-admin-pw"},
            headers=_auth_headers(login_a["token"]),
        )
        assert resp.status_code == 200
        # B 会话已失效
        assert (
            client.get("/api/auth/me", headers=_auth_headers(login_b["token"])).status_code
            == 401
        )
        # A 会话仍有效
        assert (
            client.get("/api/auth/me", headers=_auth_headers(login_a["token"])).status_code
            == 200
        )

        # 旧密码登录应失败，新密码应成功
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        ).status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "new-admin-pw"}
            ).status_code
            == 200
        )


def test_logout_revokes_current_session(client):
    """登出后当前 token 立即失效。"""
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
    ).json()
    headers = _auth_headers(login["token"])
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_sessions_list_and_revoke(client):
    """会话列表能返回并标记当前；撤销某会话后其 token 失效且非本人不能撤销。"""
    login_a = client.post(
        "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
    ).json()
    login_b = client.post(
        "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
    ).json()

    headers_a = _auth_headers(login_a["token"])
    sessions = client.get("/api/auth/sessions", headers=headers_a)
    assert sessions.status_code == 200
    rows = sessions.json()
    # B 会话（最后创建）在前，A（当前）在后，其中一个是 current
    assert len(rows) >= 2
    current = [r for r in rows if r["current"]]
    assert len(current) == 1
    assert current[0]["id"] != ""

    # 撤销 B 的会话 → B 失效，A 仍有效
    b_row = [r for r in rows if r["id"] != current[0]["id"]][0]
    assert client.delete(f"/api/auth/sessions/{b_row['id']}", headers=headers_a).status_code == 204
    assert client.get("/api/auth/me", headers=_auth_headers(login_b["token"])).status_code == 401
    assert client.get("/api/auth/me", headers=headers_a).status_code == 200


def test_admin_manages_users(settings):
    """manage 用户：非 superuser 403，superuser 可列/重设密码/调整管理员。"""
    app = create_app(settings)
    with TestClient(app) as client:
        admin = client.post(
            "/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        ).json()
        reg = _create_user(client, "victim", "victim-pass")
        admin_headers = _auth_headers(admin["token"])

        # 非管理员访问 -> 403
        assert (
            client.get("/api/auth/users", headers=_auth_headers(reg["token"])).status_code == 403
        )

        users = client.get("/api/auth/users", headers=admin_headers)
        assert users.status_code == 200
        victim = [u for u in users.json() if u["username"] == "victim"][0]

        # 管理员重设其密码 -> 旧密码 401，新密码 200
        assert (
            client.patch(
                f"/api/auth/users/{victim['id']}",
                json={"password": "reset-pw"},
                headers=admin_headers,
            ).status_code
            == 200
        )
        assert client.post(
            "/api/auth/login", json={"username": "victim", "password": "victim-pass"}
        ).status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "victim", "password": "reset-pw"}
            ).status_code
            == 200
        )


def test_cannot_remove_last_superuser(client):
    users = client.get("/api/auth/users").json()
    admin = next(user for user in users if user["username"] == "admin")
    response = client.patch(f"/api/auth/users/{admin['id']}", json={"isSuperuser": False})
    assert response.status_code == 400
    assert response.json()["detail"] == "至少保留一名平台管理员"


def test_password_recovery_no_enumeration(client):
    """找回密码：无论账号是否存在都返回同一句成功提示（防用户枚举）。"""
    existing = client.post("/api/auth/password-recovery", json={"username": "admin"})
    missing = client.post("/api/auth/password-recovery", json={"username": "no_such_user"})
    assert existing.status_code == 200
    assert missing.status_code == 200
    assert existing.json()["message"] == missing.json()["message"]
