"""自动调度完整真机 E2E：Scheduler.tick 生成/触发 → run_send 真实发送 → 终态确认。

给 oinzc 造一个「已到期」的当日计划，绑定今天全新文本模板（避免幂等重复、确保真发
一次），账号状态重置为 HEALTHY（此前被 run_send 早查误标过 auth_expired，实际登录有效），
然后手动 tick() 让调度器到点抢租约并触发真实发送，最后检查计划与任务状态。

只应在用户确认后运行（会给 oinzc 小号真实发送一条文本）。

用法：
  .venv\\Scripts\\python.exe scripts\\probe_scheduler_e2e.py
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import build_engine  # noqa: E402
from app.core.migrations import run_migrations  # noqa: E402
from app.execution.browser_manager import BrowserManager  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    AccountStatus,
    ContractTarget,
    MessageTemplate,
    SendJob,
    SendSchedule,
    ScheduleStatus,
    TemplateType,
)
from app.scheduler import Scheduler  # noqa: E402

ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"  # oinzc
TEMPLATE_NAME = "自动调度E2E"


async def main() -> None:
    settings = get_settings()
    run_migrations(settings.database_url)
    engine = build_engine(settings.database_url)
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        account = session.get(Account, ACCOUNT_ID)
        if account is None:
            raise RuntimeError("账号不存在")
        account.enabled = True
        account.status = AccountStatus.HEALTHY  # 此前被 run_send 早查误标，实际登录有效
        session.add(account)
        session.commit()
        local_tz = ZoneInfo(account.timezone)

    # 1) 今天全新文本模板（新 template_id → slot 全新，绝不幂等重复）
    with Session(engine) as session:
        tmpl = session.exec(
            select(MessageTemplate).where(MessageTemplate.name == TEMPLATE_NAME)
        ).first()
        if tmpl is None:
            tmpl = MessageTemplate(
                name=TEMPLATE_NAME,
                type=TemplateType.TEXT,
                content="{{target_name}}，自动调度E2E：今天也别忘了续火～",
            )
            session.add(tmpl)
            session.commit()
            session.refresh(tmpl)
        target = session.exec(
            select(ContractTarget).where(ContractTarget.account_id == ACCOUNT_ID)
        ).first()
        if target is None:
            raise RuntimeError("账号下没有契约对象")
        target.template_id = tmpl.id
        target.enabled = True
        session.add(target)
        session.commit()

        local_date = datetime.now(timezone.utc).astimezone(local_tz).date()
        for row in session.exec(
            select(SendSchedule).where(SendSchedule.account_id == ACCOUNT_ID)
        ).all():
            session.delete(row)  # 重建当天计划，保证到点触发一次
        session.commit()

        sch = SendSchedule(
            account_id=ACCOUNT_ID,
            business_date=local_date,
            scheduled_at=now - timedelta(minutes=5),  # 已到期
            status=ScheduleStatus.PENDING,
        )
        session.add(sch)
        session.commit()
        session.refresh(sch)
        print("LOCAL_DATE:", local_date, "| SCHEDULE:", sch.id)

    # 2) 执行器 + 调度器（不 start 循环，只手动 tick 一次）
    pw = await async_playwright().start()
    manager = BrowserManager(engine, settings)
    manager._playwright = pw
    sched = Scheduler(engine, settings, send_account=manager.run_send)

    try:
        tick_result = await sched.tick(now=now)
        await sched.wait_idle()

        with Session(engine) as session:
            row = session.get(SendSchedule, sch.id)
            jobs = session.exec(
                select(SendJob).where(SendJob.account_id == ACCOUNT_ID)
            ).all()
        print("TICK:", json.dumps(tick_result, ensure_ascii=False))
        print("SCHEDULE_STATUS:", row.status.value if row else None,
              "| last_error:", row.last_error if row else None)
        print("JOBS:", json.dumps(
            [{"status": j.status.value, "slot": j.message_slot_id} for j in jobs],
            ensure_ascii=False,
        ))
    finally:
        await manager.aclose()


if __name__ == "__main__":
    asyncio.run(main())
