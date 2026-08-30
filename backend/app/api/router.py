from fastapi import APIRouter

from .accounts import router as accounts_router
from .backup import router as backup_router
from .dashboard import router as dashboard_router
from .execution import router as execution_router
from .jobs import router as jobs_router
from .scheduler import router as scheduler_router
from .selectors import router as selectors_router
from .stats import router as stats_router
from .targets import router as targets_router
from .templates import router as templates_router

api_router = APIRouter()
api_router.include_router(accounts_router)
api_router.include_router(backup_router)
api_router.include_router(execution_router)
api_router.include_router(jobs_router)
api_router.include_router(targets_router)
api_router.include_router(templates_router)
api_router.include_router(dashboard_router)
api_router.include_router(scheduler_router)
api_router.include_router(selectors_router)
api_router.include_router(stats_router)
