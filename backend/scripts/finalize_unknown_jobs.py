"""人工核对后台账：把当日 unknown 任务核销为成功并同步状态。

用法：
  .venv\\Scripts\\python.exe scripts\\finalize_unknown_jobs.py
前提：已在抖音页面人工确认这些消息确实显示在聊天记录中。
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.models import Account, ContractTarget, JobStatus, SendJob


def main() -> None:
    settings = get_settings()
    run_migrations(settings.database_url)
    engine = build_engine(settings.database_url)

    with Session(engine) as session:
        jobs = session.exec(
            select(SendJob).where(SendJob.status == JobStatus.UNKNOWN_REQUIRES_REVIEW)
        ).all()
        now = datetime.now(timezone.utc)
        for job in jobs:
            print(f"finalize: {job.id} target={job.target_id}")
            job.status = JobStatus.SUCCEEDED
            job.confirmed_at = now
            job.updated_at = now
            session.add(job)

            target = session.get(ContractTarget, job.target_id)
            if target is not None:
                target.last_sent_at = now
                target.last_confirmed_at = now
                target.streak_days += 1
                target.updated_at = now
                session.add(target)
                print(
                    f"  target {target.nickname_snapshot}: streak_days -> {target.streak_days}"
                )

            account = session.get(Account, job.account_id)
            if account is not None:
                account.last_success_at = now
                session.add(account)
        session.commit()
        print(f"FINALIZED: {len(jobs)}")


if __name__ == "__main__":
    main()
