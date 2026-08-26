from fastapi import APIRouter, status

from ..core.database import SessionDep
from ..schemas import TemplateCreate, TemplateRead
from ..services import create_template, list_templates

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateRead])
def read_templates(session: SessionDep) -> list[TemplateRead]:
    return list_templates(session)


@router.post("", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def add_template(payload: TemplateCreate, session: SessionDep) -> TemplateRead:
    return create_template(session, payload)
