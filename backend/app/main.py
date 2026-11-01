from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from .api.health import router as health_router
from .api.router import api_router
from .core.artifacts import prune_old_artifacts
from .core.config import Settings, get_settings
from .core.database import build_engine
from .core.migrations import run_migrations
from .core.security import hash_password
from .execution.browser_manager import BrowserManager
from .models import Account, MessageTemplate, User
from .notifications import BarkNotifier
from .scheduler import Scheduler


def claim_legacy_data(settings: Settings, engine) -> None:
    """仅为旧库认领无属主数据；全新安装改由页面初始化首位管理员。"""
    with Session(engine) as session:
        orphan_accounts = session.exec(select(Account).where(Account.user_id.is_(None))).all()
        orphan_templates = session.exec(
            select(MessageTemplate).where(MessageTemplate.user_id.is_(None))
        ).all()
        if not orphan_accounts and not orphan_templates:
            return

        admin = session.exec(
            select(User).where(User.username == settings.bootstrap_username)
        ).first()
        if admin is None:
            admin = User(
                username=settings.bootstrap_username,
                password_hash=hash_password(settings.bootstrap_password),
                display_name=settings.bootstrap_username,
                is_superuser=True,  # 首启种子账号即平台管理员
            )
            session.add(admin)
            try:
                session.commit()
                session.refresh(admin)
            except Exception:
                session.rollback()
                return
        elif not admin.is_superuser:
            # 既有库升级：把种子账号提升为平台管理员，幂等修复迁移时的默认值。
            admin.is_superuser = True
            session.add(admin)
            session.commit()

        for account in orphan_accounts:
            account.user_id = admin.id
            session.add(account)
        for template in orphan_templates:
            template.user_id = admin.id
            session.add(template)
        if orphan_accounts or orphan_templates:
            session.commit()


def create_app(
    settings: Settings | None = None,
    browser_manager=None,
    scheduler: Scheduler | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = build_engine(settings.database_url)
    # 通知器由调度器与浏览器流程共享：扫码登录的结果也要能主动回音。
    notifier = BarkNotifier(base_url=settings.bark_url)
    if browser_manager is None:
        browser_manager = BrowserManager(
            engine,
            settings,
            notifier=notifier,
            auth_check_tolerance=settings.auth_check_tolerance,
            login_check_timeout=settings.login_check_timeout,
            send_cooldown=settings.send_cooldown_seconds,
            send_cooldown_jitter=settings.send_cooldown_jitter_seconds,
            scan_timeout=settings.scan_timeout_seconds,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = engine
        app.state.browser_manager = browser_manager
        app.state.settings = settings
        run_migrations(settings.database_url)
        claim_legacy_data(app.state.settings, engine)
        # 扫描进度写入 SQLite；进程重启后只收束遗留任务，不自动重开浏览器或重复扫描。
        recover_scan_tasks = getattr(browser_manager, "recover_persisted_scan_tasks", None)
        if callable(recover_scan_tasks):
            recover_scan_tasks()
        # 启动时清理过期诊断产物（保守策略：仅删文件，失败不阻断启动）
        try:
            prune_old_artifacts(settings.artifacts_dir, settings.artifact_retention_days)
        except Exception:
            pass
        # 调度器：发送执行器复用 run_send；会话状态同步复用后台扫描并只回写已有对象
        sched = scheduler
        if sched is None:
            sched = Scheduler(
                engine,
                settings,
                send_account=browser_manager.run_send,
                send_targets=browser_manager.run_send_for_targets,
                scan_account=browser_manager.run_auto_scan,
                auto_scan_interval_hours=settings.auto_scan_interval_hours,
                notifier=notifier,
                window_expiry_policy=settings.window_expiry_policy,
                notify_on_success=settings.notify_on_success,
                failure_streak_limit=settings.failure_streak_limit,
                scheduled_always_send=settings.scheduled_always_send,
                fixed_time_grace_minutes=settings.fixed_time_grace_minutes,
                auto_scan_timeout=settings.scan_timeout_seconds + 120.0,
                max_concurrent_sends=settings.max_concurrent_sends,
            )
            sched.start()
        else:
            # 注入测试/自定义调度器时也恢复数据库中的全局暂停状态。
            sched.load_persisted_state()
        app.state.scheduler = sched
        yield
        if sched is not None:
            await sched.stop()
        await browser_manager.aclose()
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    if settings.serve_frontend:
        # FastAPI 0.141+ 的 frontend 路由优先保留 API，再为 React SPA 提供 index fallback。
        app.frontend(
            "/",
            directory=settings.frontend_dist_dir,
            fallback="index.html",
            check_dir=True,
        )
    return app


app = create_app()
