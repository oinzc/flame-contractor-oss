from fastapi import APIRouter, HTTPException, Request, status

from ..core.database import SessionDep
from ..models import Account
from ..schemas import DryRunRead, LoginStatusRead

router = APIRouter(prefix="/accounts/{account_id}", tags=["execution"])


def _require_account(session: SessionDep, account_id: str) -> None:
    if session.get(Account, account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")


@router.post("/login", response_model=LoginStatusRead, status_code=status.HTTP_202_ACCEPTED)
async def start_login(account_id: str, request: Request, session: SessionDep) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.start_login(account_id)


@router.get("/login", response_model=LoginStatusRead)
async def read_login(account_id: str, request: Request, session: SessionDep) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.login_status(account_id)


@router.post("/login/cancel", response_model=LoginStatusRead)
async def cancel_login(account_id: str, request: Request, session: SessionDep) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.cancel_login(account_id)


@router.post("/dry-run", response_model=DryRunRead)
async def dry_run(account_id: str, request: Request, session: SessionDep) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.run_dry_run(account_id)
