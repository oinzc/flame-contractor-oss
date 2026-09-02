from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from sqlmodel import select

from ..core.database import SessionDep
from ..models import Account, SendSchedule
from ..schemas import SendScheduleRead, SchedulerStatusRead

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


def _today_schedules(session: SessionDep, scheduler) -> list[SendScheduleRead]:
    """聚合今日各账号的调度计划（按账号本地时区的 business_date）。"""
    records: list[SendScheduleRead] = []
    accounts = session.exec(select(Account)).all()
    for account in accounts:
        business_date = datetime.now(ZoneInfo(account.timezone)).date()
        schedule = session.exec(
            select(SendSchedule).where(
                SendSchedule.account_id == account.id,
                SendSchedule.business_date == business_date,
            )
        ).first()
        if schedule is not None:
            records.append(SendScheduleRead.model_validate(schedule))
    records.sort(key=lambda r: (r.account_id, r.business_date))
    return records


def _scheduler_read(request: Request, session: SessionDep) -> SchedulerStatusRead:
    scheduler = request.app.state.scheduler
    auto_scan_status = getattr(scheduler, "auto_scan_status", None)
    return SchedulerStatusRead(
        enabled=scheduler.enabled,
        paused=scheduler.paused,
        running_accounts=sorted(scheduler.running_accounts),
        today_schedules=_today_schedules(session, scheduler),
        auto_scan_interval_hours=getattr(scheduler, "auto_scan_interval_hours", 24),
        auto_scans=auto_scan_status() if callable(auto_scan_status) else [],
    )


@router.get("", response_model=SchedulerStatusRead)
def read_scheduler_status(request: Request, session: SessionDep) -> SchedulerStatusRead:
    return _scheduler_read(request, session)


@router.post("/pause", response_model=SchedulerStatusRead)
def pause_scheduler(request: Request, session: SessionDep) -> SchedulerStatusRead:
    scheduler = request.app.state.scheduler
    scheduler.pause()
    return _scheduler_read(request, session)


@router.post("/resume", response_model=SchedulerStatusRead)
def resume_scheduler(request: Request, session: SessionDep) -> SchedulerStatusRead:
    scheduler = request.app.state.scheduler
    scheduler.resume()
    return _scheduler_read(request, session)
