from fastapi import APIRouter, HTTPException, Query

from ..core.database import SessionDep
from ..models import JobStatus
from ..schemas import JobRead, JobsPageRead
from ..services import confirm_job_sent, list_jobs_page, release_job
from .deps import CurrentUser

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobsPageRead)
def list_jobs(
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    account_id: str | None = Query(None, alias="accountId"),
    status_filter: str | None = Query(None, alias="status"),
) -> JobsPageRead:
    """分页列出当前用户的发送任务，支持按账号与状态筛选（status 逗号分隔多个）。"""
    statuses: list[JobStatus] | None = None
    if status_filter:
        statuses = []
        for raw in status_filter.split(","):
            token = raw.strip()
            if not token:
                continue
            try:
                statuses.append(JobStatus(token))
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"未知任务状态：{token}",
                ) from exc
    return list_jobs_page(
        session,
        user.id,
        limit=limit,
        offset=offset,
        account_id=account_id,
        statuses=statuses,
    )


@router.post("/{job_id}/confirm-sent", response_model=JobRead)
def confirm_sent(job_id: str, session: SessionDep, user: CurrentUser) -> JobRead:
    """人工确认「待复核」任务的消息已实际发出（对齐成功路径，火花与统计随之更新）。"""
    return confirm_job_sent(session, job_id, user.id)


@router.post("/{job_id}/release", response_model=JobRead)
def release(job_id: str, session: SessionDep, user: CurrentUser) -> JobRead:
    """人工确认消息未发出并放行：任务回到 SCHEDULED，可再次发送。"""
    return release_job(session, job_id, user.id)
