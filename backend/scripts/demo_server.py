"""演示实例：生成一份虚构数据并以只读模式启动控制台，用于截图和体验界面。

演示实例与真实运行完全隔离：
- 数据库、浏览器 Profile、诊断产物都放在独立目录（默认 `data/demo/`），不读写真实库；
- 不加载 `.env`，Bark 通知强制关闭；
- 调度器的派发循环被替换为空操作：不会到点发送，也不会自动扫描会话；
- 除登录/登出外，所有写操作（POST/PUT/PATCH/DELETE）一律返回 403，
  因此任何按钮都不会打开浏览器或访问抖音。

所有昵称、账号、文案都是虚构的，与任何真实用户无关。

用法（在 backend/ 目录）：
  .venv/Scripts/python.exe scripts/demo_server.py            # Windows
  .venv/bin/python scripts/demo_server.py                    # Linux/macOS
  可选参数：--port 8010  --data-dir <目录>  --reseed

先执行 `cd frontend && npm run build` 生成前端产物，然后打开 http://127.0.0.1:8010/ ，
用 demo / demo-password 登录。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-password"
TZ = ZoneInfo("Asia/Shanghai")
READ_ONLY_ALLOWED = ("/api/auth/login", "/api/auth/logout")


def _utc(dt: datetime) -> datetime:
    """把带时区的时间转成库里使用的 naive UTC。"""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _local(day: date, hhmm: str, jitter_seconds: int = 0) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return _utc(moment + timedelta(seconds=jitter_seconds))


# 虚构的契约对象：(昵称, 类型, 好友级时刻, 累计天数, 官方火花天数, 火花类型, 特殊标签)
DAILY_TARGETS = [
    ("小满", "user", "07:30", 412, 412, "normal", None),
    ("阿哲", "user", None, 268, 268, "normal", None),
    ("林间有风", "user", None, 187, 187, "couple", None),
    ("周末去爬山", "user", None, 96, 96, "normal", None),
    ("豆豆不吃香菜", "user", None, 63, 63, "family", None),
    ("橘子汽水", "user", None, 41, None, "normal", "重燃中 2/3"),
    ("大学室友群", "group", None, 154, 154, "normal", None),
    ("晚自习小分队", "group", None, 22, 22, "normal", None),
]
BACKUP_TARGETS = [
    ("一只小企鹅", "user", None, 305, 305, "normal", None),
    ("海边的卡夫卡", "user", None, 77, 77, "normal", None),
    ("西瓜味的夏天", "user", None, 15, 15, "normal", None),
]
OLD_TARGETS = [
    ("旧同事老王", "user", None, 33, None, None, None),
    ("楼下咖啡店", "user", None, 8, None, None, None),
]
POOL_LINES = [
    ("今天也来续一下火 🔥", 0),
    ("周一快乐，这周也一起加油", 0b0000001),
    ("周五啦，周末有什么安排？", 0b0010000),
    ("刚看到一个好笑的视频，晚点发你", 0),
    ("晚安，明天见 🌙", 0),
]


def seed(database_url: str, now_utc: datetime) -> None:
    from sqlmodel import Session

    from app.core.database import build_engine
    from app.core.migrations import run_migrations
    from app.core.security import hash_password
    from app.models import (
        Account,
        AccountStatus,
        AppSetting,
        AttemptStatus,
        ContractTarget,
        FailureClass,
        JobStatus,
        MessageTemplate,
        ScanTask,
        ScanTaskStatus,
        ScheduleStatus,
        SendAttempt,
        SendJob,
        SendSchedule,
        TemplatePoolItem,
        TemplateType,
        User,
    )
    from app.scan_reconciliation import build_reconciliation, target_snapshot

    rng = random.Random(20261107)
    run_migrations(database_url)
    engine = build_engine(database_url)
    today = now_utc.replace(tzinfo=timezone.utc).astimezone(TZ).date()

    with Session(engine) as session:
        user = User(
            username=DEMO_USERNAME,
            password_hash=hash_password(DEMO_PASSWORD),
            display_name="演示管理员",
            is_superuser=True,
            created_at=now_utc - timedelta(days=420),
        )
        session.add(user)
        session.flush()

        text_tpl = MessageTemplate(
            user_id=user.id, name="晚安问候", type=TemplateType.TEXT,
            content="晚安，今天也续上啦 🔥", version=3,
        )
        pool_tpl = MessageTemplate(
            user_id=user.id, name="每日文案池", type=TemplateType.POOL, version=5,
        )
        sticker_tpl = MessageTemplate(
            user_id=user.id, name="比心表情", type=TemplateType.STICKER,
            sticker_name="比心", sticker_label="比心", sticker_category="默认",
        )
        session.add_all([text_tpl, pool_tpl, sticker_tpl])
        session.flush()
        for index, (line, mask) in enumerate(POOL_LINES):
            session.add(TemplatePoolItem(
                template_id=pool_tpl.id, content=line, weekday_mask=mask,
                last_used_at=now_utc - timedelta(days=index + 1),
            ))

        accounts = [
            (Account(
                user_id=user.id, display_name="日常号", status=AccountStatus.HEALTHY,
                send_time_hhmm="21:30", douyin_nickname="晚风",
                created_at=now_utc - timedelta(days=410),
            ), DAILY_TARGETS, pool_tpl),
            (Account(
                user_id=user.id, display_name="备用号", status=AccountStatus.HEALTHY,
                send_window_start="20:00", send_window_end="22:00", douyin_nickname="山海",
                created_at=now_utc - timedelta(days=300),
            ), BACKUP_TARGETS, text_tpl),
            (Account(
                user_id=user.id, display_name="旧手机号", status=AccountStatus.AUTH_EXPIRED,
                send_time_hhmm="22:00", douyin_nickname="旧号",
                created_at=now_utc - timedelta(days=200),
            ), OLD_TARGETS, sticker_tpl),
        ]

        for account, target_rows, template in accounts:
            session.add(account)
            session.flush()
            healthy = account.status == AccountStatus.HEALTHY
            scan_at = now_utc - timedelta(minutes=rng.randint(18, 42))
            targets: list[ContractTarget] = []
            for idx, (name, kind, hhmm, streak, spark, spark_kind, label) in enumerate(target_rows):
                is_group = kind == "group"
                target = ContractTarget(
                    account_id=account.id,
                    nickname_snapshot=name,
                    conversation_type=kind,
                    platform_user_id=None if is_group else f"demo-uid-{account.display_name}-{idx:02d}",
                    platform_handle=None if is_group else f"demo_{idx:02d}{rng.randint(1000, 9999)}",
                    platform_conversation_id=f"demo-conv-{account.display_name}-{idx:02d}" if is_group else None,
                    template_id=sticker_tpl.id if (healthy and idx == 2) else template.id,
                    send_time_hhmm=hhmm,
                    streak_days=streak,
                    spark_days=spark,
                    spark_kind=spark_kind,
                    spark_label=label,
                    spark_scanned_at=scan_at if spark is not None or label else None,
                    created_at=account.created_at + timedelta(days=idx),
                )
                session.add(target)
                targets.append(target)
            session.flush()

            # 历史发送记录：最近 14 天，绝大多数成功，夹杂少量需人工复核或失败的记录
            history_days = 14 if healthy else 9
            last_success = None
            for offset in range(history_days, -1, -1):
                day = today - timedelta(days=offset)
                if not healthy and offset < 5:
                    continue  # 登录失效后不再有发送
                is_today = offset == 0
                account_time = account.send_time_hhmm or "21:" + f"{rng.randint(0, 50):02d}"
                schedule_at = _local(day, account_time)
                if is_today:
                    session.add(SendSchedule(
                        account_id=account.id, business_date=day, scheduled_at=schedule_at,
                        status=ScheduleStatus.PENDING, created_at=_local(day, "00:00", 5),
                    ))
                else:
                    session.add(SendSchedule(
                        account_id=account.id, business_date=day, scheduled_at=schedule_at,
                        status=ScheduleStatus.SUCCEEDED, attempt_count=1,
                        started_at=schedule_at, finished_at=schedule_at + timedelta(minutes=2),
                        created_at=_local(day, "00:00", 5),
                    ))
                for idx, target in enumerate(targets):
                    target_time = target.send_time_hhmm or account_time
                    sent_at = _local(day, target_time, 20 + idx * rng.randint(4, 9))
                    if is_today and sent_at > now_utc:
                        continue
                    status = JobStatus.SUCCEEDED
                    failure = None
                    if offset == 6 and idx == 3:
                        status, failure = JobStatus.UNKNOWN_REQUIRES_REVIEW, FailureClass.UNKNOWN_OUTCOME
                    elif offset == 11 and idx == 1:
                        status, failure = JobStatus.FAILED_TERMINAL, FailureClass.NETWORK
                    job = SendJob(
                        account_id=account.id, target_id=target.id,
                        template_id=target.template_id, business_date=day,
                        message_slot_id=f"t{target.template_id[:8]}v1",
                        status=status, scheduled_at=sent_at, attempt_count=1,
                        failure_class=failure,
                        confirmed_at=sent_at + timedelta(seconds=6) if status == JobStatus.SUCCEEDED else None,
                        created_at=sent_at - timedelta(seconds=30), updated_at=sent_at + timedelta(seconds=6),
                    )
                    session.add(job)
                    session.flush()
                    session.add(SendAttempt(
                        job_id=job.id, attempt_no=1,
                        stage=JobStatus.CONFIRMING if status != JobStatus.FAILED_TERMINAL else JobStatus.OPENING_CHAT,
                        status=AttemptStatus.SUCCEEDED if status == JobStatus.SUCCEEDED else AttemptStatus.FAILED,
                        failure_class=failure, started_at=sent_at,
                        finished_at=sent_at + timedelta(seconds=6),
                    ))
                    if status == JobStatus.SUCCEEDED:
                        target.last_sent_at = sent_at
                        target.last_confirmed_at = sent_at + timedelta(seconds=6)
                        last_success = max(last_success or sent_at, sent_at)
            account.last_success_at = last_success
            account.last_verified_at = scan_at if healthy else now_utc - timedelta(days=5)
            if not healthy:
                account.failure_streak = 1

            # 会话扫描：最近一次自动同步 + 一次带新会话的手动扫描
            if healthy:
                snapshots = [target_snapshot(t) for t in targets]
                # 让最近一次同步里有一条「状态变化」：第二位好友的官方天数在扫描前少一天
                snapshots[1]["previous"]["days"] = (targets[1].spark_days or 1) - 1
                entries = []
                for t in targets:
                    entries.append({
                        "title": t.nickname_snapshot,
                        "platform_user_id": t.platform_user_id,
                        "platform_handle": t.platform_handle,
                        "platform_conversation_id": t.platform_conversation_id,
                        "conversation_type": t.conversation_type,
                        "identity_status": "bound" if t.platform_user_id else "not_observed",
                        "identity_source": "unique_id" if t.platform_user_id else None,
                        "identity_candidate_count": 1 if t.platform_user_id else 0,
                        "conversation_status": "bound" if t.platform_conversation_id else "not_observed",
                        "conversation_candidate_count": 1 if t.platform_conversation_id else 0,
                        "preview": "[表情]" if t.template_id == sticker_tpl.id else "今天也来续一下火 🔥",
                        "streak_days": t.spark_days if not t.spark_label else None,
                        "streak_kind": t.spark_kind,
                        "streak_label": t.spark_label,
                    })
                new_entries = [
                    {"title": title, "conversation_type": "user", "preview": preview,
                     "streak_days": days, "streak_kind": "normal" if days else None,
                     "identity_status": "not_observed", "conversation_status": "not_observed"}
                    for title, preview, days in [
                        ("新认识的朋友", "你好呀", None),
                        ("健身搭子", "明天几点去？", 5),
                    ]
                ]
                for minutes_ago, source, rows in [
                    (60 * 26, "manual", entries + new_entries),
                    ((now_utc - scan_at).total_seconds() / 60, "automatic", entries),
                ]:
                    started = now_utc - timedelta(minutes=minutes_ago)
                    session.add(ScanTask(
                        account_id=account.id, source=source, status=ScanTaskStatus.COMPLETED,
                        total=len(rows), completed=len(rows), stage="扫描完成", round=4,
                        overall="ok", entries_json=json.dumps(rows, ensure_ascii=False),
                        reconciliation_json=json.dumps(
                            build_reconciliation(snapshots, rows), ensure_ascii=False
                        ),
                        started_at=started, finished_at=started + timedelta(seconds=48),
                        updated_at=started + timedelta(seconds=48),
                    ))

        for key, value in {
            "scheduler.paused": "0",
            "scheduler.auto_scan_interval_hours": "6",
            "scheduler.scheduled_always_send": "0",
            "scheduler.fixed_time_grace_minutes": "30",
        }.items():
            session.add(AppSetting(key=key, value=value))
        session.commit()
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Flame Contractor 演示实例（虚构数据、只读）")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data" / "demo")
    parser.add_argument("--reseed", action="store_true", help="删除演示目录后重新生成数据")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    if data_dir.name != "demo" and not (data_dir / ".flame-demo").exists():
        parser.error("为避免误删真实数据，--data-dir 的目录名必须是 demo")
    if args.reseed and data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".flame-demo").touch()
    db_path = data_dir / "demo.db"
    database_url = f"sqlite:///{db_path.as_posix()}"

    # 演示实例不读取 .env，并显式关闭通知；所有路径都指向演示目录。
    for key in list(os.environ):
        if key.startswith("FLAME_"):
            del os.environ[key]

    from app.core.config import Settings

    settings = Settings(
        _env_file=None,
        database_url=database_url,
        profiles_dir=data_dir / "profiles",
        artifacts_dir=data_dir / "artifacts",
        serve_frontend=True,
        bark_url=None,
        notify_on_success=False,
        secret_key="flame-contractor-demo-only-secret-key-0000",
    )
    if not (settings.frontend_dist_dir / "index.html").exists():
        sys.exit("未找到前端构建产物，请先在 frontend/ 执行 npm run build")

    if not db_path.exists():
        seed(database_url, datetime.now(timezone.utc).replace(tzinfo=None))
        print(f"已生成演示数据：{db_path}")

    import uvicorn
    from fastapi.responses import JSONResponse

    import app.main as app_main
    from app.scheduler import Scheduler

    async def _refuse(*_args, **_kwargs):
        raise RuntimeError("演示实例不执行发送或扫描")

    class DemoScheduler(Scheduler):
        """保留状态读取，派发循环什么都不做：不会发送，也不会扫描。"""

        def __init__(self, engine, settings, *_args, **kwargs) -> None:
            kwargs.update(
                send_account=_refuse, send_targets=_refuse, scan_account=_refuse,
                notifier=None, auto_scan_interval_hours=6,
            )
            super().__init__(engine, settings, **kwargs)

        async def tick(self, now=None) -> dict:  # noqa: ARG002
            return {"dispatched": 0}

    # create_app 在 lifespan 里按模块名构造并启动调度器；替换为演示版本，
    # 这样总览会显示「运行中 / 下次触发」，但派发循环不会做任何事。
    app_main.Scheduler = DemoScheduler
    app = app_main.create_app(settings)

    @app.middleware("http")
    async def read_only(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path not in READ_ONLY_ALLOWED:
            return JSONResponse({"detail": "演示模式只读：不会执行发送、扫描或修改数据"}, status_code=403)
        return await call_next(request)

    print(f"演示控制台：http://127.0.0.1:{args.port}/  账号 {DEMO_USERNAME} / {DEMO_PASSWORD}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
