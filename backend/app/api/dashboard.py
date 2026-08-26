from fastapi import APIRouter

from ..core.database import SessionDep
from ..schemas import DashboardSummary
from ..services import get_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardSummary)
def read_dashboard(session: SessionDep) -> DashboardSummary:
    return get_dashboard(session)
