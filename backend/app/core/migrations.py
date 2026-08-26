from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def run_migrations(database_url: str) -> None:
    """升级数据库到最新版本；空库时自动建表。"""
    if database_url.startswith("sqlite"):
        database = database_url.split("///", 1)[-1]
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
