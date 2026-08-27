import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from .execution.message_renderer import render_message
from .models import (
    Account,
    AccountStatus,
    ContractTarget,
    JobStatus,
    MessageTemplate,
    SendJob,
    utc_now,
)
from .schemas import (
    AccountCreate,
    AccountPatch,
    AccountRead,
    DashboardSummary,
    JobRead,
    TargetCreate,
    TargetPatch,
    TargetRead,
    TemplateCreate,
    TemplateRead,
)

TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
ALLOWED_TEMPLATE_VARIABLES = {"target_name", "streak_days", "weekday", "date"}

PENDING_JOB_STATUSES = (
    JobStatus.SCHEDULED,
    JobStatus.LEASED,
    JobStatus.OPENING_CHAT,
    JobStatus.SENDING,
    JobStatus.CONFIRMING,
)

ATTENTION_ACCOUNT_STATUSES = (
    AccountStatus.AUTH_EXPIRED,
    AccountStatus.VERIFICATION_REQUIRED,
    AccountStatus.DOM_BROKEN,
)


def _validate_template_content(content: str) -> None:
    tokens = TEMPLATE_TOKEN_RE.findall(content)
    unknown = sorted({token for token in tokens if token not in ALLOWED_TEMPLATE_VARIABLES})
    if unknown:
        raise HTTPException(status_code=422, detail=f"不支持的模板变量: {', '.join(unknown)}")
    stripped = TEMPLATE_TOKEN_RE.sub("", content)
    if "{" in stripped or "}" in stripped:
        raise HTTPException(status_code=422, detail="模板包含不支持的语法")


# ---------------------------------------------------------------- accounts


def list_accounts(session: Session) -> list[AccountRead]:
    accounts = session.exec(select(Account).order_by(Account.created_at)).all()
    return [AccountRead.model_validate(account) for account in accounts]


def create_account(session: Session, payload: AccountCreate) -> AccountRead:
    existing = session.exec(
        select(Account).where(Account.display_name == payload.display_name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="账号显示名称已存在")

    account = Account.model_validate(payload)
    session.add(account)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="账号显示名称已存在") from None
    session.refresh(account)
    return AccountRead.model_validate(account)


def patch_account(session: Session, account_id: str, payload: AccountPatch) -> AccountRead:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(account, field, value)
    account.updated_at = utc_now()

    session.add(account)
    session.commit()
    session.refresh(account)
    return AccountRead.model_validate(account)


# ---------------------------------------------------------------- templates


def list_templates(session: Session) -> list[TemplateRead]:
    templates = session.exec(select(MessageTemplate).order_by(MessageTemplate.created_at)).all()
    return [TemplateRead.model_validate(template) for template in templates]


def create_template(session: Session, payload: TemplateCreate) -> TemplateRead:
    existing = session.exec(
        select(MessageTemplate).where(MessageTemplate.name == payload.name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="模板名称已存在")

    _validate_template_content(payload.content)

    template = MessageTemplate.model_validate(payload)
    session.add(template)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="模板名称已存在") from None
    session.refresh(template)
    return TemplateRead.model_validate(template)


# ---------------------------------------------------------------- targets


def list_targets(session: Session, account_id: str | None = None) -> list[TargetRead]:
    statement = select(ContractTarget).order_by(ContractTarget.created_at)
    if account_id:
        statement = statement.where(ContractTarget.account_id == account_id)
    targets = session.exec(statement).all()
    return [TargetRead.model_validate(target) for target in targets]


def create_target(session: Session, payload: TargetCreate) -> TargetRead:
    account = session.get(Account, payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if payload.template_id and session.get(MessageTemplate, payload.template_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")

    target = ContractTarget.model_validate(payload)
    session.add(target)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="该账号下已存在同名契约对象") from None
    session.refresh(target)
    return TargetRead.model_validate(target)


def delete_target(session: Session, target_id: str) -> None:
    target = session.get(ContractTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="好友不存在")

    session.delete(target)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="该好友已有任务记录，不能删除；可改为暂停使用",
        ) from None


def patch_target(session: Session, target_id: str, payload: TargetPatch) -> TargetRead:
    """局部更新契约对象。当前仅支持火花天数手动校准（参考 ScriptCat）。"""
    target = session.get(ContractTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="好友不存在")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(target, field, value)
    target.updated_at = utc_now()

    session.add(target)
    session.commit()
    session.refresh(target)
    return TargetRead.model_validate(target)


# ---------------------------------------------------------------- send preview


def send_preview(session: Session, account_id: str) -> dict:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    targets = session.exec(
        select(ContractTarget)
        .where(ContractTarget.account_id == account_id, ContractTarget.enabled)
        .order_by(ContractTarget.created_at)
    ).all()
    business_date = datetime.now(ZoneInfo(account.timezone)).date()

    items: list[dict] = []
    for target in targets:
        template = (
            session.get(MessageTemplate, target.template_id) if target.template_id else None
        )
        if template is not None and template.enabled:
            message = render_message(
                template.content,
                target_name=target.nickname_snapshot,
                streak_days=target.streak_days,
                timezone=account.timezone,
            )
            items.append(
                {
                    "target_id": target.id,
                    "target_name": target.nickname_snapshot,
                    "template_id": template.id,
                    "message": message,
                    "reason": None,
                }
            )
        else:
            items.append(
                {
                    "target_id": target.id,
                    "target_name": target.nickname_snapshot,
                    "template_id": None,
                    "message": None,
                    "reason": "未绑定可用模板",
                }
            )

    return {
        "account_id": account_id,
        "business_date": business_date.isoformat(),
        "items": items,
    }


# ---------------------------------------------------------------- dashboard


def get_dashboard(session: Session) -> DashboardSummary:
    account_count = session.scalar(select(func.count()).select_from(Account)) or 0
    healthy_count = (
        session.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.status == AccountStatus.HEALTHY)
        )
        or 0
    )
    active_contracts = (
        session.scalar(
            select(func.count()).select_from(ContractTarget).where(ContractTarget.enabled)
        )
        or 0
    )

    today = date.today()
    today_succeeded = (
        session.scalar(
            select(func.count())
            .select_from(SendJob)
            .where(
                SendJob.business_date == today,
                SendJob.status == JobStatus.SUCCEEDED,
            )
        )
        or 0
    )
    today_pending = (
        session.scalar(
            select(func.count())
            .select_from(SendJob)
            .where(
                SendJob.business_date == today,
                SendJob.status.in_(PENDING_JOB_STATUSES),
            )
        )
        or 0
    )
    attention_accounts = (
        session.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.status.in_(ATTENTION_ACCOUNT_STATUSES))
        )
        or 0
    )
    attention_jobs = (
        session.scalar(
            select(func.count())
            .select_from(SendJob)
            .where(SendJob.status == JobStatus.UNKNOWN_REQUIRES_REVIEW)
        )
        or 0
    )

    accounts = session.exec(select(Account).order_by(Account.created_at)).all()
    recent_jobs = session.exec(
        select(SendJob).order_by(SendJob.updated_at.desc()).limit(8)
    ).all()

    return DashboardSummary(
        account_count=account_count,
        healthy_account_count=healthy_count,
        active_contract_count=active_contracts,
        today_succeeded=today_succeeded,
        today_pending=today_pending,
        needs_attention=attention_accounts + attention_jobs,
        accounts=[AccountRead.model_validate(account) for account in accounts],
        recent_jobs=[JobRead.model_validate(job) for job in recent_jobs],
    )
