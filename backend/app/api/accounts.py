from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from ..core.database import SessionDep
from ..schemas import AccountCreate, AccountPatch, AccountRead
from ..services import create_account, delete_account, list_accounts, patch_account

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


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_account(account_id: str, request: Request, session: SessionDep) -> Response:
    delete_account(session, account_id, request.app.state.settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
