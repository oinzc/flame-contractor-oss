from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from ..core.database import SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request, session: SessionDep) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
        database = "ready"
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据库不可用") from exc

    settings = request.app.state.settings
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.version,
        "database": database,
    }
