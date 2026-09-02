from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from ..core.database import SessionDep
from ..models import Account
from ..schemas import (
    DryRunRead,
    IdentityHydrationProgressRead,
    IdentityHydrationStart,
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


@router.post("/scan-chats/cancel", response_model=ScanProgressRead)
async def cancel_scan_chats(
    account_id: str,
    request: Request,
    session: SessionDep,
    task_id: str = Query(..., alias="taskId"),
) -> dict:
    """取消后台会话扫描；保留已发现结果，供查看或手动导入。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.cancel_scan_chats(account_id, task_id)


@router.get("/scan-chats/latest", response_model=ScanProgressRead | None)
def read_latest_scan_chats(
    account_id: str,
    request: Request,
    session: SessionDep,
) -> dict | None:
    """读取最近一次扫描快照；后端重启后仍可恢复任务坞。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return manager.latest_scan_chats(account_id)


@router.post(
    "/identity-hydration/start",
    response_model=IdentityHydrationProgressRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_identity_hydration(
    account_id: str,
    payload: IdentityHydrationStart,
    request: Request,
    session: SessionDep,
) -> dict:
    """为已存在的普通好友补采稳定身份；只打开会话，不发送消息。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.start_identity_hydration(account_id, payload.target_ids)


@router.get(
    "/identity-hydration/progress",
    response_model=IdentityHydrationProgressRead,
)
def read_identity_hydration_progress(
    account_id: str,
    request: Request,
    session: SessionDep,
    task_id: str = Query(..., alias="taskId"),
) -> dict:
    """读取身份补采进度；切换页面或刷新后仍可继续查看。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return manager.identity_hydration_progress(account_id, task_id)


@router.post(
    "/identity-hydration/cancel",
    response_model=IdentityHydrationProgressRead,
)
async def cancel_identity_hydration(
    account_id: str,
    request: Request,
    session: SessionDep,
    task_id: str = Query(..., alias="taskId"),
) -> dict:
    """取消身份补采；已完成的安全回填保留。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.cancel_identity_hydration(account_id, task_id)


@router.get(
    "/identity-hydration/latest",
    response_model=IdentityHydrationProgressRead | None,
)
def read_latest_identity_hydration(
    account_id: str,
    request: Request,
    session: SessionDep,
) -> dict | None:
    """读取最近一次身份补采快照；用于前端刷新后恢复任务坞。"""
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return manager.latest_identity_hydration(account_id)


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
async def send_now(
    account_id: str,
    request: Request,
    session: SessionDep,
    force: bool = Query(
        False,
        description="手动强制发送时绕过今日已成功记录；不绕过身份核验和未知结果保护",
    ),
) -> dict:
    _require_account(session, account_id)
    manager = request.app.state.browser_manager
    return await manager.run_send(account_id, force=force)


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
