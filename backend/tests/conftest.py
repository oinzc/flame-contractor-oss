from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        profiles_dir=tmp_path / "profiles",
        artifacts_dir=tmp_path / "artifacts",
    )


@pytest.fixture(autouse=True)
def _authed_testclient(monkeypatch):
    """让所有直接用 `with TestClient(app) as client:` 的测试也自动登录 admin。

    业务 API 现要求 Bearer token；统一在 TestClient 进入上下文（lifespan 已跑、
    admin 已种子）后自动带上 admin 的 Authorization 头，避免逐个测试文件手动加。
    """
    original_enter = TestClient.__enter__

    def patched_enter(self):
        result = original_enter(self)
        resp = self.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        if resp.status_code == 200:
            self.headers["Authorization"] = f"Bearer {resp.json()['token']}"
        return result

    monkeypatch.setattr("fastapi.testclient.TestClient.__enter__", patched_enter)


@pytest.fixture
def client(settings) -> Generator[TestClient, None, None]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        # 依赖 autouse fixture 自动带上 admin token
        yield test_client


@pytest.fixture
def engine(settings):
    db_engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)
    yield db_engine
    db_engine.dispose()
