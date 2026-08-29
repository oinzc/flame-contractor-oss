from fastapi import APIRouter, HTTPException

from ..backup import export_backup, import_backup
from ..core.database import SessionDep
from ..schemas import BackupImportResult, BackupRead

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("", response_model=BackupRead)
def read_backup(session: SessionDep) -> BackupRead:
    """导出全量契约与运行记录 JSON（不含任何凭据；Profile 登录态走文件系统备份）。"""
    return export_backup(session)


@router.post("/import", response_model=BackupImportResult)
def restore_backup(payload: BackupRead, session: SessionDep) -> BackupImportResult:
    """按 id upsert 导入：空库=完整恢复，已有库=无损合并。"""
    try:
        return import_backup(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
