from fastapi import APIRouter, status

from ..core.database import SessionDep
from ..schemas import TargetCreate, TargetRead
from ..services import create_target, list_targets

router = APIRouter(prefix="/targets", tags=["targets"])


@router.get("", response_model=list[TargetRead])
def read_targets(session: SessionDep, account_id: str | None = None) -> list[TargetRead]:
    return list_targets(session, account_id)


@router.post("", response_model=TargetRead, status_code=status.HTTP_201_CREATED)
def add_target(payload: TargetCreate, session: SessionDep) -> TargetRead:
    return create_target(session, payload)
