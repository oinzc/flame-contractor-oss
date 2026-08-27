from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.health import router as health_router
from .api.router import api_router
from .core.config import Settings, get_settings
from .core.database import build_engine
from .core.migrations import run_migrations
from .execution.browser_manager import BrowserManager
from .notifications import BarkNotifier
from .scheduler import Scheduler


def create_app(
    settings: Settings | None = None,
    browser_manager=None,
    scheduler: Scheduler | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = build_engine(settings.database_url)
    if browser_manager is None:
        browser_manager = BrowserManager(engine, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = engine
        app.state.browser_manager = browser_manager
        run_migrations(settings.database_url)
        # 调度器：发送执行器复用 browser_manager.run_send（账号级批量、幂等、终态确认）
        sched = scheduler
        if sched is None:
            sched = Scheduler(
                engine,
                settings,
                send_account=browser_manager.run_send,
                notifier=BarkNotifier(base_url=settings.bark_url),
            )
            sched.start()
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
    return app


app = create_app()
