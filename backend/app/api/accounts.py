from fastapi import APIRouter, HTTPException, Query, Response, status

from ..core.database import SessionDep
from ..core.config import get_settings
from ..schemas import AccountCreate, AccountPatch, AccountRead
from ..services import create_account, list_accounts, patch_account

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountRead])
def read_accounts(session: SessionDep) -> list[AccountRead]:
    return list_accounts(session)


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def add_account(payload: AccountCreate, session: SessionDep) -> AccountRead:
    return create_account(session, payload)


@router.patch("/{account_id}", response_model=AccountRead)
def update_account(
    account_id: str, payload: AccountPatch, session: SessionDep
) -> AccountRead:
    return patch_account(session, account_id, payload)
