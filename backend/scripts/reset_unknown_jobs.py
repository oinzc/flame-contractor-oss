"""把当日 unknown 任务安全降级为可重试（仅限人工确认消息未发出后使用）。

用法：
  .venv\\Scripts\\python.exe scripts\\reset_unknown_jobs.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.models import FailureClass, JobStatus, SendJob


def main() -> None:
    settings = get_settings()
    run_migrations(settings.database_url)
    engine = build_engine(settings.database_url)

    with Session(engine) as session:
        jobs = session.exec(
            select(SendJob).where(SendJob.status == JobStatus.UNKNOWN_REQUIRES_REVIEW)
        ).all()
        for job in jobs:
            print(f"reset: {job.id} slot={job.message_slot_id} date={job.business_date}")
            job.status = JobStatus.FAILED_RETRYABLE
            job.failure_class = FailureClass.OTHER
            job.updated_at = job.updated_at.__class__.now(job.updated_at.tzinfo)
            session.add(job)
        session.commit()
        print(f"JOBS_RESET: {len(jobs)}")


if __name__ == "__main__":
    main()
