from fastapi import APIRouter

from ..core.database import SessionDep
from ..schemas import JobRead
from ..services import confirm_job_sent, release_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/{job_id}/confirm-sent", response_model=JobRead)
def confirm_sent(job_id: str, session: SessionDep) -> JobRead:
    """人工确认「待复核」任务的消息已实际发出（对齐成功路径，火花与统计随之更新）。"""
    return confirm_job_sent(session, job_id)


@router.post("/{job_id}/release", response_model=JobRead)
def release(job_id: str, session: SessionDep) -> JobRead:
    """人工确认消息未发出并放行：任务回到 SCHEDULED，可再次发送。"""
    return release_job(session, job_id)
