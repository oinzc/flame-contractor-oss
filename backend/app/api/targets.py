from fastapi import APIRouter, HTTPException, Response, status

from ..core.database import SessionDep
from ..schemas import TargetBatchCreate, TargetBatchRead, TargetCreate, TargetPatch, TargetRead
from ..services import create_target, create_targets_batch, delete_target, list_targets, patch_target
from .deps import CurrentUser

router = APIRouter(prefix="/targets", tags=["targets"])


@router.get("", response_model=list[TargetRead])
def read_targets(
    session: SessionDep, user: CurrentUser, account_id: str | None = None
) -> list[TargetRead]:
    return list_targets(session, user.id, account_id)


@router.post("", response_model=TargetRead, status_code=status.HTTP_201_CREATED)
def add_target(payload: TargetCreate, session: SessionDep, user: CurrentUser) -> TargetRead:
    return create_target(session, payload, user.id)


@router.post("/batch", response_model=TargetBatchRead, status_code=status.HTTP_201_CREATED)
def add_targets_batch(
    payload: TargetBatchCreate, session: SessionDep, user: CurrentUser
) -> TargetBatchRead:
    return create_targets_batch(session, payload, user.id)


@router.patch("/{target_id}", response_model=TargetRead)
def update_target(
    target_id: str, payload: TargetPatch, session: SessionDep, user: CurrentUser
) -> TargetRead:
    return patch_target(session, target_id, payload, user.id)


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_target(target_id: str, session: SessionDep, user: CurrentUser) -> Response:
    delete_target(session, target_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
