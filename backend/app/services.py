import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, func, select

from .core.config import Settings
from .execution.message_renderer import render_message
from .execution.pool import pick_pool_item
from .execution.profile_store import remove_profile_dir
from .models import (
    Account,
    AccountStatus,
    ContractTarget,
    JobStatus,
    MessageTemplate,
    SendJob,
    SendSchedule,
    TemplatePoolItem,
    TemplateType,
    utc_now,
)
from .schemas import (
    AccountCreate,
    AccountPatch,
    AccountRead,
    DashboardSummary,
    JobRead,
    PoolItemIn,
    PoolItemRead,
    TargetCreate,
    TargetPatch,
    TargetRead,
    TemplateCreate,
    TemplatePatch,
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
    if "display_name" in data:
        existing = session.exec(
            select(Account).where(Account.display_name == data["display_name"])
        ).first()
        if existing and existing.id != account_id:
            raise HTTPException(status_code=409, detail="账号显示名称已存在")

    for field, value in data.items():
        setattr(account, field, value)
    account.updated_at = utc_now()

    session.add(account)
    session.commit()
    session.refresh(account)
    return AccountRead.model_validate(account)


def delete_account(session: Session, account_id: str, settings: Settings) -> None:
    """删除账号。被好友/任务引用时拒绝；无引用物理删除并清理浏览器档案。

    SendSchedule 是调度器每日自动生成的派生数据（非用户数据），随账号一并清除，
    否则账号一经调度便永远无法删除。SendJob 是发送历史（审计数据），保留拦截。
    """
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.status == AccountStatus.RUNNING:
        raise HTTPException(status_code=409, detail="账号正在执行任务，请稍后再删除")

    target_refs = session.exec(
        select(func.count(ContractTarget.id)).where(ContractTarget.account_id == account_id)
    ).one()
    job_refs = session.exec(
        select(func.count(SendJob.id)).where(SendJob.account_id == account_id)
    ).one()
    if target_refs or job_refs:
        raise HTTPException(
            status_code=409,
            detail=(
                f"账号仍有 {target_refs} 个好友、{job_refs} 条发送任务，不能删除；可先停用账号"
            ),
        )

    session.execute(delete(SendSchedule).where(SendSchedule.account_id == account_id))
    session.flush()
    session.delete(account)
    session.commit()
    remove_profile_dir(settings, account_id)


# ---------------------------------------------------------------- templates


def list_templates(session: Session) -> list[TemplateRead]:
    templates = session.exec(select(MessageTemplate).order_by(MessageTemplate.created_at)).all()
    pool_items_by_template: dict[str, list[TemplatePoolItem]] = {}
    template_ids = [template.id for template in templates]
    if template_ids:
        items = session.exec(
            select(TemplatePoolItem)
            .where(TemplatePoolItem.template_id.in_(template_ids))
            .order_by(TemplatePoolItem.created_at)
        ).all()
        for item in items:
            pool_items_by_template.setdefault(item.template_id, []).append(item)
    reads: list[TemplateRead] = []
    for template in templates:
        read = TemplateRead.model_validate(template)
        if template.type == TemplateType.POOL:
            read.pool_items = [
                PoolItemRead.model_validate(item)
                for item in pool_items_by_template.get(template.id, [])
            ]
        reads.append(read)
    return reads


def _validate_pool_items(items: list[PoolItemIn]) -> None:
    for item in items:
        if not item.content.strip():
            raise HTTPException(status_code=422, detail="文案池候选不能为空")
        if not 0 <= item.weekday_mask <= 127:
            raise HTTPException(status_code=422, detail="weekday_mask 取值 0-127")


def _validate_template_fields(
    template_type: TemplateType,
    *,
    content: str,
    image_path: str | None,
    sticker_name: str | None,
    pool_items: list[PoolItemIn] | None,
) -> None:
    """按模板类型校验内容字段。create 与 patch 共用，规则只此一份。"""
    if template_type == TemplateType.TEXT:
        if not content or not content.strip():
            raise HTTPException(status_code=422, detail="文本模板内容不能为空")
        _validate_template_content(content)
    elif template_type == TemplateType.IMAGE:
        if not image_path or not image_path.strip():
            raise HTTPException(status_code=422, detail="图片模板必须指定图片路径")
    elif template_type == TemplateType.STICKER:
        if not sticker_name or not sticker_name.strip():
            raise HTTPException(status_code=422, detail="表情模板必须指定表情名称(stickerName)")
    elif template_type == TemplateType.POOL:
        if not pool_items:
            raise HTTPException(status_code=422, detail="文案池模板需要至少一条候选文案")
        _validate_pool_items(pool_items)


def create_template(session: Session, payload: TemplateCreate) -> TemplateRead:
    existing = session.exec(
        select(MessageTemplate).where(MessageTemplate.name == payload.name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="模板名称已存在")

    _validate_template_fields(
        payload.type,
        content=payload.content,
        image_path=payload.image_path,
        sticker_name=payload.sticker_name,
        pool_items=payload.pool_items,
    )

    template = MessageTemplate.model_validate(payload)
    session.add(template)
    session.flush()  # 先取 template.id 再挂文案池候选
    for item in payload.pool_items or []:
        session.add(
            TemplatePoolItem(
                template_id=template.id,
                content=item.content.strip(),
                weekday_mask=item.weekday_mask,
                enabled=item.enabled,
            )
        )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="模板名称已存在") from None
    session.refresh(template)
    read = TemplateRead.model_validate(template)
    if payload.type == TemplateType.POOL:
        items = session.exec(
            select(TemplatePoolItem)
            .where(TemplatePoolItem.template_id == template.id)
            .order_by(TemplatePoolItem.created_at)
        ).all()
        read.pool_items = [PoolItemRead.model_validate(item) for item in items]
    return read


def _replace_pool_items(
    session: Session, template: MessageTemplate, items: list[PoolItemIn]
) -> None:
    """整体替换文案池候选。content 相同的候选继承 last_used_at，避免轮换节奏被打乱。"""
    old_items = session.exec(
        select(TemplatePoolItem).where(TemplatePoolItem.template_id == template.id)
    ).all()
    last_used_by_content = {item.content: item.last_used_at for item in old_items}
    for item in old_items:
        session.delete(item)
    session.flush()
    for item in items:
        session.add(
            TemplatePoolItem(
                template_id=template.id,
                content=item.content.strip(),
                weekday_mask=item.weekday_mask,
                enabled=item.enabled,
                last_used_at=last_used_by_content.get(item.content.strip()),
            )
        )


# 发送内容相关字段：任一变化都提升版本号，使当日幂等键变化、新文案可重新发送
_TEMPLATE_CONTENT_FIELDS = (
    "content",
    "image_path",
    "sticker_name",
    "sticker_label",
    "sticker_category",
    "sticker_fallback_index",
)


def patch_template(session: Session, template_id: str, payload: TemplatePatch) -> TemplateRead:
    template = session.get(MessageTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")

    fields_set = payload.model_fields_set

    if "name" in fields_set:
        dup = session.exec(
            select(MessageTemplate).where(MessageTemplate.name == payload.name)
        ).first()
        if dup and dup.id != template_id:
            raise HTTPException(status_code=409, detail="模板名称已存在")

    # 合并 patch 与库中现值，按模板类型整体校验（防止 patch 后模板进入无效状态）
    merged_content = payload.content if "content" in fields_set else template.content
    merged_image_path = payload.image_path if "image_path" in fields_set else template.image_path
    merged_sticker_name = (
        payload.sticker_name if "sticker_name" in fields_set else template.sticker_name
    )
    new_pool = payload.pool_items if "pool_items" in fields_set else None
    merged_pool = new_pool
    if merged_pool is None and template.type == TemplateType.POOL:
        merged_pool = [
            PoolItemIn(content=item.content, weekday_mask=item.weekday_mask, enabled=item.enabled)
            for item in session.exec(
                select(TemplatePoolItem)
                .where(TemplatePoolItem.template_id == template.id)
                .order_by(TemplatePoolItem.created_at)
            ).all()
        ]

    _validate_template_fields(
        template.type,
        content=merged_content,
        image_path=merged_image_path,
        sticker_name=merged_sticker_name,
        pool_items=merged_pool,
    )

    # 发送内容是否实际变化：变了才 bump version，使当日幂等键变化、新文案可重新发送
    content_changed = any(
        field in fields_set and getattr(payload, field) != getattr(template, field)
        for field in _TEMPLATE_CONTENT_FIELDS
    )
    if template.type == TemplateType.POOL and new_pool is not None:
        old_items = session.exec(
            select(TemplatePoolItem).where(TemplatePoolItem.template_id == template.id)
        ).all()
        old_signature = sorted((i.content, i.weekday_mask, i.enabled) for i in old_items)
        new_signature = sorted(
            (i.content, i.weekday_mask, i.enabled) for i in new_pool
        )
        if old_signature != new_signature:
            content_changed = True

    if new_pool is not None:
        _replace_pool_items(session, template, new_pool)
    for field in fields_set:
        if field == "pool_items":
            continue
        setattr(template, field, getattr(payload, field))
    if content_changed:
        template.version += 1
    template.updated_at = utc_now()

    session.add(template)
    session.commit()
    session.refresh(template)

    read = TemplateRead.model_validate(template)
    if template.type == TemplateType.POOL:
        items = session.exec(
            select(TemplatePoolItem)
            .where(TemplatePoolItem.template_id == template.id)
            .order_by(TemplatePoolItem.created_at)
        ).all()
        read.pool_items = [PoolItemRead.model_validate(item) for item in items]
    return read


def delete_template(session: Session, template_id: str) -> None:
    template = session.get(MessageTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")

    target_refs = session.exec(
        select(func.count(ContractTarget.id)).where(ContractTarget.template_id == template_id)
    ).one()
    job_refs = session.exec(
        select(func.count(SendJob.id)).where(SendJob.template_id == template_id)
    ).one()
    if target_refs or job_refs:
        raise HTTPException(
            status_code=409,
            detail=f"模板仍被 {target_refs} 个好友绑定、{job_refs} 条发送任务引用，不能删除；可改为停用",
        )

    for item in session.exec(
        select(TemplatePoolItem).where(TemplatePoolItem.template_id == template_id)
    ).all():
        session.delete(item)
    session.delete(template)
    session.commit()


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
            if template.type == TemplateType.IMAGE:
                message = f"[图片] {template.image_path or ''}"
            elif template.type == TemplateType.STICKER:
                message = f"[表情] {template.sticker_name or ''}"
            elif template.type == TemplateType.POOL:
                # 预览只展示今天会发的候选，不消耗轮换顺序（consume=False）
                weekday_index = datetime.now(ZoneInfo(account.timezone)).weekday()
                pool_item = pick_pool_item(session, template.id, weekday_index)
                if pool_item is None:
                    message = "[文案池] 今日无可用候选"
                else:
                    message = render_message(
                        pool_item.content,
                        target_name=target.nickname_snapshot,
                        streak_days=target.streak_days,
                        timezone=account.timezone,
                    )
            else:
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
