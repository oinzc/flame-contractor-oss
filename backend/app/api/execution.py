from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from ..core.database import SessionDep
from ..models import Account
from ..schemas import (
    DryRunRead,
    LoginStatusRead,
    ScanChatsRead,
    ScanProgressRead,
    SendPreviewRead,
    SendProgressRead,
    SendRead,
)
from ..services import send_preview

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


@router.post("/scan-chats", response_model=ScanChatsRead)
async def scan_chats(account_id: str, request: Request, session: SessionDep) -> dict:
    """兼容旧客户端的同步扫描接口。控制台使用下面的后台任务接口。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.scan_chats(account_id)


@router.post(
    "/scan-chats/start",
    response_model=ScanProgressRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_scan_chats(account_id: str, request: Request, session: SessionDep) -> dict:
    """发起后台会话扫描；前端可切换页面后继续轮询进度。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.start_scan_chats(account_id)


@router.get("/scan-chats/progress", response_model=ScanProgressRead)
def read_scan_chats_progress(
    account_id: str,
    request: Request,
    session: SessionDep,
    task_id: str = Query(..., alias="taskId"),
) -> dict:
    """读取后台会话扫描进度与完成后的扫描结果。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return manager.scan_chats_progress(account_id, task_id)


@router.get("/send/preview", response_model=SendPreviewRead)
def read_send_preview(account_id: str, session: SessionDep) -> dict:
    _require_account(session, account_id)
    return send_preview(session, account_id)


@router.get("/send/progress", response_model=SendProgressRead)
def read_send_progress(account_id: str, request: Request, session: SessionDep) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return manager.send_progress(account_id)


@router.post("/send", response_model=SendRead)
async def send_now(account_id: str, request: Request, session: SessionDep) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.run_send(account_id)


# ---------------------------------------------------------------- M8-4 截图扫码登录


@router.post("/login/qr", response_model=LoginStatusRead, status_code=status.HTTP_202_ACCEPTED)
async def start_qr_login(account_id: str, request: Request, session: SessionDep) -> dict:
    """headless 截图扫码登录：不弹有头窗口，前端轮询 /login/qr/image 展示二维码。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.start_qr_login(account_id)


@router.get("/login/qr", response_model=LoginStatusRead)
def read_qr_login(account_id: str, request: Request, session: SessionDep) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return manager.qr_login_status(account_id)


@router.post("/login/qr/cancel", response_model=LoginStatusRead)
async def cancel_qr_login(account_id: str, request: Request, session: SessionDep) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.cancel_login(account_id)


@router.get("/login/qr/image")
def read_qr_image(account_id: str, request: Request, session: SessionDep):
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    path = manager.qr_image_path(account_id)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="二维码截图尚未生成")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})
