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


@pytest.fixture
def client(settings) -> Generator[TestClient, None, None]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def engine(settings):
    db_engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)
    yield db_engine
    db_engine.dispose()
