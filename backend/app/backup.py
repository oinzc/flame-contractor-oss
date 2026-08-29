"""M8-3 备份导入/导出：契约（账号/好友/模板含文案池）与运行记录的 JSON 往返。

设计要点：
- 导出为单一 JSON（schema_version=1），含 accounts/templates(+pool_items)/
  targets/schedules/jobs/attempts 全量；不含任何凭据（Profile 登录态属文件系统
  层，不经 API 导出）。
- 导入为**按 id upsert**（存在则更新、缺失则插入）：对空库即完整恢复，
  对已有库是无损合并；不做「清空重建」模式，避免误操作放大破坏面。
- 插入顺序按外键依赖：accounts → templates → pool_items → targets →
  schedules → jobs → attempts。
"""

from datetime import date, datetime, timezone

from sqlalchemy import Date as _SaDate, DateTime as _SaDateTime
from sqlmodel import Session, select

from .models import (
    Account,
    ContractTarget,
    MessageTemplate,
    SendAttempt,
    SendJob,
    SendSchedule,
    TemplatePoolItem,
)
from .schemas import BackupData, BackupImportResult, BackupRead

BACKUP_SCHEMA_VERSION = 1
# 导入顺序（外键依赖决定）
_IMPORT_ORDER = [
    ("accounts", Account),
    ("templates", MessageTemplate),
    ("pool_items", TemplatePoolItem),
    ("targets", ContractTarget),
    ("schedules", SendSchedule),
    ("jobs", SendJob),
    ("attempts", SendAttempt),
]


def export_backup(session: Session) -> BackupRead:
    data = {}
    for key, model in _IMPORT_ORDER:
        rows = session.exec(select(model)).all()
        data[key] = [
            row.model_dump(mode="json") for row in rows
        ]
    return BackupRead(
        app="flame-contractor",
        schema_version=BACKUP_SCHEMA_VERSION,
        exported_at=datetime.now(timezone.utc),
        data=BackupData(**data),
    )


def _coerce_row(model, row: dict) -> dict:
    """JSON 往返的 datetime/date 是 ISO 字符串，按 SQLAlchemy 列类型还原。"""
    table = model.__table__
    for key, value in list(row.items()):
        column = table.c.get(key)
        if column is None or not isinstance(value, str):
            continue
        if isinstance(column.type, _SaDateTime):
            row[key] = datetime.fromisoformat(value)
        elif isinstance(column.type, _SaDate):
            row[key] = date.fromisoformat(value)
    return row


def import_backup(session: Session, backup: BackupRead) -> BackupImportResult:
    if backup.schema_version != BACKUP_SCHEMA_VERSION:
        raise ValueError(f"不支持的备份版本: {backup.schema_version}")

    payload = backup.data.model_dump()
    inserted = {key: 0 for key, _ in _IMPORT_ORDER}
    updated = {key: 0 for key, _ in _IMPORT_ORDER}

    for key, model in _IMPORT_ORDER:
        for row in payload.get(key) or []:
            row = _coerce_row(model, dict(row))
            row_id = row.get("id")
            existing = session.get(model, row_id) if row_id else None
            if existing is not None:
                for field, value in row.items():
                    setattr(existing, field, value)
                session.add(existing)
                updated[key] += 1
            else:
                session.add(model(**row))
                inserted[key] += 1
    session.commit()
    return BackupImportResult(inserted=inserted, updated=updated)
