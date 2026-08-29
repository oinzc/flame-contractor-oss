"""M8-2 运行统计：按日聚合、连续成功天数、总体成功率；CSV 导出。"""

import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlmodel import Session, func, select

from ..core.database import SessionDep
from ..models import JobStatus, SendJob

router = APIRouter(prefix="/stats", tags=["stats"])

_SUCCESS_STATUSES = (JobStatus.SUCCEEDED,)
_FAILED_STATUSES = (JobStatus.FAILED_RETRYABLE, JobStatus.FAILED_TERMINAL)
_UNKNOWN_STATUSES = (JobStatus.UNKNOWN_REQUIRES_REVIEW,)
_DEFAULT_WINDOW_DAYS = 30


def _daily_rows(session: Session, since: date) -> list[dict]:
    rows = session.exec(
        select(SendJob).where(SendJob.business_date >= since).order_by(SendJob.business_date)
    ).all()
    buckets: dict[date, dict] = {}
    for job in rows:
        bucket = buckets.setdefault(
            job.business_date,
            {"date": job.business_date, "total": 0, "succeeded": 0, "failed": 0, "unknown": 0},
        )
        bucket["total"] += 1
        if job.status in _SUCCESS_STATUSES:
            bucket["succeeded"] += 1
        elif job.status in _FAILED_STATUSES:
            bucket["failed"] += 1
        elif job.status in _UNKNOWN_STATUSES:
            bucket["unknown"] += 1
    return [buckets[key] for key in sorted(buckets)]


def _success_streaks(daily: list[dict]) -> tuple[int, int]:
    """（当前连续成功天数，最长连续成功天数）。

    一天视为「成功日」当且仅当当日 succeeded ≥ 1 且 failed == 0 且 unknown == 0。
    中间断档（无任务的天）会打断连续。
    """
    current = best = 0
    prev_date: date | None = None
    for row in daily:
        is_success = row["succeeded"] >= 1 and row["failed"] == 0 and row["unknown"] == 0
        consecutive = prev_date is not None and row["date"] == prev_date + timedelta(days=1)
        if is_success:
            current = current + 1 if consecutive else 1
            best = max(best, current)
        else:
            current = 0
        prev_date = row["date"]
    return current, best


@router.get("/summary")
def stats_summary(session: SessionDep, days: int = _DEFAULT_WINDOW_DAYS) -> dict:
    since = date.today() - timedelta(days=max(1, min(days, 365)))
    daily = _daily_rows(session, since)
    current_streak, best_streak = _success_streaks(daily)
    total = sum(row["total"] for row in daily)
    succeeded = sum(row["succeeded"] for row in daily)
    return {
        "window_days": max(1, min(days, 365)),
        "daily": daily,
        "currentSuccessStreak": current_streak,
        "bestSuccessStreak": best_streak,
        "totalJobs": total,
        "succeededJobs": succeeded,
        "successRate": round(succeeded / total, 4) if total else None,
    }


@router.get("/export.csv")
def export_jobs_csv(session: SessionDep) -> StreamingResponse:
    """任务记录 CSV 导出（按业务日期排序，含失败分类与确认时间）。"""
    rows = session.exec(
        select(SendJob).order_by(SendJob.business_date, SendJob.created_at)
    ).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["business_date", "account_id", "target_id", "status", "failure_class",
         "attempt_count", "scheduled_at", "confirmed_at", "message_slot_id"]
    )
    for job in rows:
        writer.writerow(
            [
                job.business_date.isoformat(),
                job.account_id,
                job.target_id,
                job.status.value if hasattr(job.status, "value") else job.status,
                job.failure_class.value if job.failure_class and hasattr(job.failure_class, "value") else (job.failure_class or ""),
                job.attempt_count,
                job.scheduled_at.isoformat() if job.scheduled_at else "",
                job.confirmed_at.isoformat() if job.confirmed_at else "",
                job.message_slot_id,
            ]
        )
    buffer.seek(0)
    today = date.today().isoformat()
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="flame-jobs-{today}.csv"'},
    )
