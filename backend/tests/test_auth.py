"""平台账号(User)认证与多租户数据隔离测试。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import create_app
from app.models import Account, MessageTemplate, User


def _register(client: TestClient, username: str, password: str = "pw123456") -> dict:
    resp = client.post("/api/auth/register", json={"username": username, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_seed_default_user_exists(client, settings):
    """首启自动种子 admin，密码 admin。"""
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["username"] == "admin"


def test_register_login_me_roundtrip(client):
    resp = client.post("/api/auth/register", json={"username": "carol", "password": "abc123"})
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    head = _auth_headers(token)

    me = client.get("/api/auth/me", headers=head)
    assert me.status_code == 200
    assert me.json()["username"] == "carol"

    login = client.post("/api/auth/login", json={"username": "carol", "password": "abc123"})
    assert login.status_code == 200
    assert login.json()["token"]


def test_login_wrong_password_401(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_register_duplicate_username_409(client):
    _register(client, "dup")
    resp = client.post("/api/auth/register", json={"username": "dup", "password": "x"})
    assert resp.status_code == 409


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
        alice = client.post("/api/auth/register", json={"username": "alice", "password": "pw"}).json()
        bob = client.post("/api/auth/register", json={"username": "bob", "password": "pw"}).json()

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
        alice = client.post("/api/auth/register", json={"username": "alice2", "password": "pw"}).json()
        bob = client.post("/api/auth/register", json={"username": "bob2", "password": "pw"}).json()

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
        alice = client.post("/api/auth/register", json={"username": "alice3", "password": "pw"}).json()
        bob = client.post("/api/auth/register", json={"username": "bob3", "password": "pw"}).json()

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
        alice = client.post("/api/auth/register", json={"username": "alice4", "password": "pw"}).json()
        bob = client.post("/api/auth/register", json={"username": "bob4", "password": "pw"}).json()

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
