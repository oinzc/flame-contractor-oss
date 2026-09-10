from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.main import create_app

TEST_ADMIN_PASSWORD = "admin123"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        profiles_dir=tmp_path / "profiles",
        artifacts_dir=tmp_path / "artifacts",
    )


@pytest.fixture(autouse=True)
def _authed_testclient(monkeypatch):
    """让业务测试使用一次性初始化后的管理员会话。

    单独验证首启流程的测试可在 app.state.disable_test_auto_auth 置 True 后跳过。
    """
    original_enter = TestClient.__enter__

    def patched_enter(self):
        result = original_enter(self)
        if getattr(self.app.state, "disable_test_auto_auth", False):
            return result
        setup = self.get("/api/auth/setup")
        if setup.status_code == 200 and setup.json()["needsSetup"]:
            resp = self.post(
                "/api/auth/register",
                json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
            )
        else:
            resp = self.post(
                "/api/auth/login",
                json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
            )
            # 部分持久化测试会在应用启动前直接写入无属主业务数据；旧库认领流程
            # 会使用 Settings 中的兼容密码创建管理员。
            if resp.status_code == 401:
                resp = self.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "admin"},
                )
        if resp.status_code == 200:
            self.headers["Authorization"] = f"Bearer {resp.json()['token']}"
        elif resp.status_code == 201:
            self.headers["Authorization"] = f"Bearer {resp.json()['token']}"
        return result

    monkeypatch.setattr("fastapi.testclient.TestClient.__enter__", patched_enter)


@pytest.fixture
def client(settings) -> Generator[TestClient, None, None]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        # 依赖 autouse fixture 自动完成首次初始化并带上 admin token
        yield test_client


@pytest.fixture
def engine(settings):
    db_engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)
    yield db_engine
    db_engine.dispose()
