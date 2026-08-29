from fastapi import APIRouter, Response, status

from ..core.database import SessionDep
from ..schemas import TemplateCreate, TemplatePatch, TemplateRead
from ..services import (
    create_template,
    delete_template,
    list_templates,
    patch_template,
)

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateRead])
def read_templates(session: SessionDep) -> list[TemplateRead]:
    return list_templates(session)


@router.post("", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def add_template(payload: TemplateCreate, session: SessionDep) -> TemplateRead:
    return create_template(session, payload)


@router.patch("/{template_id}", response_model=TemplateRead)
def update_template(
    template_id: str, payload: TemplatePatch, session: SessionDep
) -> TemplateRead:
    return patch_template(session, template_id, payload)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_template(template_id: str, session: SessionDep) -> Response:
    delete_template(session, template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
