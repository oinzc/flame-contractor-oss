from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  # 确保所有表注册进 metadata
import sqlmodel  # noqa: F401  # 供迁移脚本使用 sqlmodel.sql.sqltypes
from sqlmodel import SQLModel

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers 默认 True，会把此刻已存在的 logger 全部置为 disabled。
    # 迁移在应用启动时执行、而 app.* 的模块 logger 在导入时创建，于是整个应用的
    # logger.warning/error 全被吞掉（通知发送失败、扫码登录失败现场、发送诊断都收不到）。
    # 只借用 alembic.ini 的格式与级别，不动应用已有的 logger。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from app.core.config import get_settings

    return get_settings().database_url


def _ensure_sqlite_parent(url: str) -> None:
    if url.startswith("sqlite"):
        database = url.split("///", 1)[-1]
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_url = _database_url()
    _ensure_sqlite_parent(database_url)
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = database_url
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
