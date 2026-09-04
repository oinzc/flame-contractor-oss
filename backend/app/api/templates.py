from fastapi import APIRouter, Response, status

from ..core.database import SessionDep
from ..schemas import TemplateCreate, TemplatePatch, TemplateRead
from ..services import (
    create_template,
    delete_template,
    list_templates,
    patch_template,
)
from .deps import CurrentUser

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateRead])
def read_templates(session: SessionDep, user: CurrentUser) -> list[TemplateRead]:
    return list_templates(session, user.id)


@router.post("", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def add_template(payload: TemplateCreate, session: SessionDep, user: CurrentUser) -> TemplateRead:
    return create_template(session, payload, user.id)


@router.patch("/{template_id}", response_model=TemplateRead)
def update_template(
    template_id: str, payload: TemplatePatch, session: SessionDep, user: CurrentUser
) -> TemplateRead:
    return patch_template(session, template_id, payload, user.id)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_template(template_id: str, session: SessionDep, user: CurrentUser) -> Response:
    delete_template(session, template_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
