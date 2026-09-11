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
    AttemptStatus,
    ContractTarget,
    JobStatus,
    MessageTemplate,
    SendAttempt,
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
    BatchTargetItem,
    DashboardSummary,
    JobRead,
    PoolItemIn,
    PoolItemRead,
    TargetCreate,
    TargetBatchCreate,
    TargetBatchRead,
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


def list_accounts(session: Session, user_id: str) -> list[AccountRead]:
    accounts = session.exec(
        select(Account).where(Account.user_id == user_id).order_by(Account.created_at)
    ).all()
    return [AccountRead.model_validate(account) for account in accounts]


def create_account(session: Session, payload: AccountCreate, user_id: str) -> AccountRead:
    existing = session.exec(
        select(Account).where(
            Account.user_id == user_id,
            Account.display_name == payload.display_name,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="账号显示名称已存在")

    account = Account(
        user_id=user_id,
        display_name=payload.display_name,
        timezone=payload.timezone,
        send_window_start=payload.send_window_start,
        send_window_end=payload.send_window_end,
        send_time_hhmm=payload.send_time_hhmm,
    )
    session.add(account)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="账号显示名称已存在") from None
    session.refresh(account)
    return AccountRead.model_validate(account)


def patch_account(
    session: Session, account_id: str, payload: AccountPatch, user_id: str
) -> AccountRead:
    account = session.get(Account, account_id)
    if account is None or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="账号不存在")

    data = payload.model_dump(exclude_unset=True)
    if "display_name" in data:
        existing = session.exec(
            select(Account).where(
                Account.user_id == user_id,
                Account.display_name == data["display_name"],
            )
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


def delete_account(
    session: Session, account_id: str, settings: Settings, user_id: str
) -> None:
    """删除账号。被好友/任务引用时拒绝；无引用物理删除并清理浏览器档案。

    SendSchedule 是调度器每日自动生成的派生数据（非用户数据），随账号一并清除，
    否则账号一经调度便永远无法删除。SendJob 是发送历史（审计数据），保留拦截。
    软删除的好友（deleted_at 非空）不视为「活跃引用」：两者都无引用时才放行，
    放行前会一并清除这些 job-less 的软删行，避免物理删账号触发外键约束。
    """
    account = session.get(Account, account_id)
    if account is None or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.status == AccountStatus.RUNNING:
        raise HTTPException(status_code=409, detail="账号正在执行任务，请稍后再删除")

    target_refs = session.exec(
        select(func.count(ContractTarget.id)).where(
            ContractTarget.account_id == account_id,
            ContractTarget.deleted_at.is_(None),
        )
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

    # 清除 soft-deleted 好友行（此路径下它们必然无 SendJob 引用，否则已被流水线拦截）
    session.execute(
        delete(ContractTarget).where(
            ContractTarget.account_id == account_id,
            ContractTarget.deleted_at.is_not(None),
        )
    )
    session.execute(delete(SendSchedule).where(SendSchedule.account_id == account_id))
    session.flush()
    session.delete(account)
    session.commit()
    remove_profile_dir(settings, account_id)


# ---------------------------------------------------------------- templates


def list_templates(session: Session, user_id: str) -> list[TemplateRead]:
    templates = session.exec(
        select(MessageTemplate)
        .where(MessageTemplate.user_id == user_id)
        .order_by(MessageTemplate.created_at)
    ).all()
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


def create_template(
    session: Session, payload: TemplateCreate, user_id: str
) -> TemplateRead:
    existing = session.exec(
        select(MessageTemplate).where(
            MessageTemplate.user_id == user_id,
            MessageTemplate.name == payload.name,
        )
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

    template = MessageTemplate(
        user_id=user_id,
        name=payload.name,
        type=payload.type,
        content=payload.content,
        image_path=payload.image_path,
        sticker_name=payload.sticker_name,
        sticker_label=payload.sticker_label,
        sticker_category=payload.sticker_category,
        sticker_fallback_index=payload.sticker_fallback_index,
    )
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


def patch_template(
    session: Session, template_id: str, payload: TemplatePatch, user_id: str
) -> TemplateRead:
    template = session.get(MessageTemplate, template_id)
    if template is None or template.user_id != user_id:
        raise HTTPException(status_code=404, detail="模板不存在")

    fields_set = payload.model_fields_set

    if "name" in fields_set:
        dup = session.exec(
            select(MessageTemplate).where(
                MessageTemplate.user_id == user_id,
                MessageTemplate.name == payload.name,
            )
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


def delete_template(session: Session, template_id: str, user_id: str) -> None:
    template = session.get(MessageTemplate, template_id)
    if template is None or template.user_id != user_id:
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


def list_targets(
    session: Session, user_id: str, account_id: str | None = None
) -> list[TargetRead]:
    """列出当前用户可见的契约好友；默认排除软删除行（deleted_at 非空视为已删除）。"""
    statement = (
        select(ContractTarget)
        .join(Account, ContractTarget.account_id == Account.id)
        .where(Account.user_id == user_id)
        .order_by(ContractTarget.created_at)
    )
    if account_id:
        statement = statement.where(ContractTarget.account_id == account_id)
    targets = session.exec(statement).all()
    return [
        TargetRead.model_validate(target)
        for target in targets
        if target.deleted_at is None
    ]


def _account_belongs_to_user(session: Session, account_id: str, user_id: str) -> bool:
    account = session.get(Account, account_id)
    return account is not None and account.user_id == user_id


def _template_belongs_to_user(
    session: Session, template_id: str, user_id: str
) -> bool:
    template = session.get(MessageTemplate, template_id)
    return template is not None and template.user_id == user_id


def create_target(
    session: Session, payload: TargetCreate, user_id: str
) -> TargetRead:
    account = session.get(Account, payload.account_id)
    if account is None or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="账号不存在")
    if payload.template_id and not _template_belongs_to_user(
        session, payload.template_id, user_id
    ):
        raise HTTPException(status_code=404, detail="模板不存在")
    if payload.platform_user_id:
        same_platform_user = session.exec(
            select(ContractTarget).where(
                ContractTarget.account_id == payload.account_id,
                ContractTarget.platform_user_id == payload.platform_user_id,
            )
        ).first()
        if same_platform_user is not None:
            if same_platform_user.deleted_at is not None:
                # 删除后重新添加同一好友：复活原行而非新建，保留审计与身份唯一性。
                return _revive_target(session, same_platform_user)
            raise HTTPException(status_code=409, detail="该账号下已存在同一抖音用户")
    if payload.platform_conversation_id:
        same_platform_conversation = session.exec(
            select(ContractTarget).where(
                ContractTarget.account_id == payload.account_id,
                ContractTarget.platform_conversation_id == payload.platform_conversation_id,
            )
        ).first()
        if same_platform_conversation is not None:
            if same_platform_conversation.deleted_at is not None:
                return _revive_target(session, same_platform_conversation)
            raise HTTPException(status_code=409, detail="该账号下已存在同一群会话")

    nickname_existing = session.exec(
        select(ContractTarget).where(
            ContractTarget.account_id == payload.account_id,
            ContractTarget.nickname_snapshot == payload.nickname_snapshot,
        )
    ).first()
    if nickname_existing is not None:
        if nickname_existing.deleted_at is not None:
            return _revive_target(session, nickname_existing)
        raise HTTPException(status_code=409, detail="该账号下已存在同名契约对象")

    target = ContractTarget.model_validate(payload)
    session.add(target)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="该账号下已存在同名契约对象") from None
    session.refresh(target)
    return TargetRead.model_validate(target)


def _revive_target(session: Session, target: ContractTarget) -> TargetRead:
    """复活一枚软删除好友：清除标记并保持启用，返回读模型。"""
    target.deleted_at = None
    target.enabled = True
    target.updated_at = utc_now()
    session.add(target)
    session.commit()
    session.refresh(target)
    return TargetRead.model_validate(target)


def create_targets_batch(
    session: Session, payload: TargetBatchCreate, user_id: str
) -> TargetBatchRead:
    """批量添加好友。逐条独立提交：重复项跳过记录，其余失败也不中断整批。"""
    account = session.get(Account, payload.account_id)
    if account is None or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="账号不存在")
    if payload.template_id and not _template_belongs_to_user(
        session, payload.template_id, user_id
    ):
        raise HTTPException(status_code=404, detail="模板不存在")

    created: list[str] = []
    refreshed: list[str] = []
    duplicates: list[str] = []
    failed: list[str] = []
    identity_bound: list[str] = []
    identity_unresolved: list[str] = []
    identity_ambiguous: list[str] = []
    conversation_bound: list[str] = []
    conversation_unresolved: list[str] = []
    conversation_ambiguous: list[str] = []

    def track_identity(
        item: BatchTargetItem,
        nickname: str,
        platform_user_id: str | None,
        platform_conversation_id: str | None,
    ) -> None:
        if platform_user_id:
            identity_bound.append(nickname)
        elif platform_conversation_id:
            conversation_bound.append(nickname)
        elif item.conversation_type == "group":
            if item.conversation_status == "ambiguous":
                conversation_ambiguous.append(nickname)
            elif item.conversation_status == "not_observed":
                conversation_unresolved.append(nickname)
        elif item.identity_status == "ambiguous":
            identity_ambiguous.append(nickname)
        elif item.identity_status == "not_observed":
            identity_unresolved.append(nickname)

    for item in payload.items:
        nickname = item.nickname
        platform_user_id = item.platform_user_id.strip() if item.platform_user_id else None
        platform_handle = item.platform_handle.strip() if item.platform_handle else None
        platform_conversation_id = (
            item.platform_conversation_id.strip()
            if item.platform_conversation_id
            else None
        )
        conversation_type = item.conversation_type or (
            "group" if platform_conversation_id else None
        )
        has_spark_snapshot = any(
            field in item.model_fields_set
            for field in ("spark_days", "spark_kind", "spark_label")
        )
        existing = (
            session.exec(
                select(ContractTarget).where(
                    ContractTarget.account_id == payload.account_id,
                    ContractTarget.platform_user_id == platform_user_id,
                )
            ).first()
            if platform_user_id
            else None
        )
        if platform_conversation_id:
            existing = session.exec(
                select(ContractTarget).where(
                    ContractTarget.account_id == payload.account_id,
                    ContractTarget.platform_conversation_id == platform_conversation_id,
                )
            ).first()
        nickname_existing = session.exec(
            select(ContractTarget).where(
                ContractTarget.account_id == payload.account_id,
                ContractTarget.nickname_snapshot == nickname,
            )
        ).first()
        if existing is None:
            existing = nickname_existing
        if existing is not None:
            # 命中软删除行：删除后同名/同号再次导入应复活该好友，而非因唯一约束跳过。
            revived = existing.deleted_at is not None
            if revived:
                existing.deleted_at = None
                existing.enabled = True
                existing.updated_at = utc_now()
                session.add(existing)
                session.commit()
                refreshed.append(f"{nickname}（已恢复）")
            if (
                platform_user_id
                and existing.platform_user_id
                and existing.platform_user_id != platform_user_id
            ):
                failed.append(f"{nickname}：稳定身份与已有好友不一致")
                continue
            if (
                platform_conversation_id
                and existing.platform_conversation_id
                and existing.platform_conversation_id != platform_conversation_id
            ):
                failed.append(f"{nickname}：稳定会话身份与已有好友不一致")
                continue
            if platform_conversation_id and existing.platform_user_id:
                failed.append(f"{nickname}：群会话身份与已有好友不一致")
                continue
            if platform_user_id and existing.platform_conversation_id:
                failed.append(f"{nickname}：用户身份与已有群会话不一致")
                continue
            if platform_user_id and existing.platform_user_id is None:
                existing.platform_user_id = platform_user_id
            if platform_handle:
                existing.platform_handle = platform_handle
            if platform_conversation_id and existing.platform_conversation_id is None:
                existing.platform_conversation_id = platform_conversation_id
            if conversation_type:
                existing.conversation_type = conversation_type
            same_stable_identity = (
                platform_user_id is not None
                and existing.platform_user_id == platform_user_id
            ) or (
                platform_conversation_id is not None
                and existing.platform_conversation_id == platform_conversation_id
            )
            if same_stable_identity and existing.nickname_snapshot != nickname:
                nickname_conflict = session.exec(
                    select(ContractTarget).where(
                        ContractTarget.account_id == payload.account_id,
                        ContractTarget.nickname_snapshot == nickname,
                        ContractTarget.id != existing.id,
                    )
                ).first()
                if nickname_conflict is not None:
                    failed.append(f"{nickname}：昵称已绑定其他好友")
                    session.rollback()
                    continue
                existing.nickname_snapshot = nickname
            if revived:
                track_identity(item, nickname, platform_user_id, platform_conversation_id)
                if has_spark_snapshot:
                    now = utc_now()
                    existing.spark_days = item.spark_days
                    existing.spark_kind = item.spark_kind
                    existing.spark_label = item.spark_label
                    existing.spark_scanned_at = now
                    existing.updated_at = now
                    session.add(existing)
                    session.commit()
                continue
            # 扫描导入可以刷新已有好友的官方徽标快照；本地累计天数保持不变。
            if (
                platform_user_id
                or platform_handle
                or platform_conversation_id
                or has_spark_snapshot
            ):
                now = utc_now()
                if has_spark_snapshot:
                    existing.spark_days = item.spark_days
                    existing.spark_kind = item.spark_kind
                    existing.spark_label = item.spark_label
                    existing.spark_scanned_at = now
                existing.updated_at = now
                session.add(existing)
                session.commit()
                refreshed.append(nickname)
            else:
                duplicates.append(nickname)
            track_identity(item, nickname, platform_user_id, platform_conversation_id)
            continue
        try:
            scanned_at = (
                utc_now()
                if has_spark_snapshot
                else None
            )
            session.add(
                ContractTarget(
                    account_id=payload.account_id,
                    platform_user_id=platform_user_id,
                    platform_handle=platform_handle,
                    platform_conversation_id=platform_conversation_id,
                    conversation_type=conversation_type,
                    nickname_snapshot=nickname,
                    template_id=payload.template_id,
                    # 扫描读到的官方火花天数作为初始值；之后仍走本地累计
                    streak_days=item.streak_days or 0,
                    spark_days=item.spark_days,
                    spark_kind=item.spark_kind,
                    spark_label=item.spark_label,
                    spark_scanned_at=scanned_at,
                )
            )
            session.commit()
            created.append(nickname)
            track_identity(item, nickname, platform_user_id, platform_conversation_id)
        except IntegrityError:
            session.rollback()
            duplicates.append(nickname)
            track_identity(item, nickname, platform_user_id, platform_conversation_id)
        except Exception as exc:  # 单条失败不拖垮整批
            session.rollback()
            failed.append(f"{nickname}：{exc.__class__.__name__}")
    return TargetBatchRead(
        created=created,
        refreshed=refreshed,
        duplicates=duplicates,
        failed=failed,
        identity_bound=identity_bound,
        identity_unresolved=identity_unresolved,
        identity_ambiguous=identity_ambiguous,
        conversation_bound=conversation_bound,
        conversation_unresolved=conversation_unresolved,
        conversation_ambiguous=conversation_ambiguous,
    )


def _target_belongs_to_user(
    session: Session, target: ContractTarget, user_id: str
) -> bool:
    account = session.get(Account, target.account_id)
    return account is not None and account.user_id == user_id


def delete_target(session: Session, target_id: str, user_id: str) -> None:
    """软删除好友：打 deleted_at 标记而非物理删行。

    SendJob 是发送历史（审计数据），物理删行会触发外键冲突或历史丢失。
    软删除既保住审计与身份唯一性，也让好友真正从列表/发送/扫描中消失；
    之后同名/同号再次导入会自动复活该行。deleted 行保留 enabled 原值不参与发送。
    """
    target = session.get(ContractTarget, target_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(status_code=404, detail="好友不存在")
    if not _target_belongs_to_user(session, target, user_id):
        raise HTTPException(status_code=404, detail="好友不存在")

    target.deleted_at = utc_now()
    target.updated_at = utc_now()
    session.add(target)
    session.commit()


def patch_target(
    session: Session, target_id: str, payload: TargetPatch, user_id: str
) -> TargetRead:
    """局部更新契约对象：官方火花快照校准、启停、换绑模板。"""
    target = session.get(ContractTarget, target_id)
    if target is None or not _target_belongs_to_user(session, target, user_id):
        raise HTTPException(status_code=404, detail="好友不存在")

    data = payload.model_dump(exclude_unset=True)
    if data.get("template_id") is not None and session.get(MessageTemplate, data["template_id"]) is None:
        raise HTTPException(status_code=404, detail="模板不存在")

    official_status_updated = "spark_days" in data or "spark_label" in data
    if official_status_updated:
        # 官方数字与特殊文字状态互斥。即使客户端只提交其中一项，也要清除旧形态，
        # 避免「105 天」与「重燃中 1/3」同时残留。手动校准不触碰本地 streak_days。
        target.spark_days = data.pop("spark_days", None)
        target.spark_label = data.pop("spark_label", None)
        if target.spark_label == "无火花":
            target.spark_kind = None
        target.spark_scanned_at = utc_now()

    for field, value in data.items():
        setattr(target, field, value)
    target.updated_at = utc_now()

    session.add(target)
    session.commit()
    session.refresh(target)
    return TargetRead.model_validate(target)


# ---------------------------------------------------------------- manual job review


def _record_manual_action(session: Session, job_id: str, stage: str) -> None:
    """人工接管动作留痕：作为一条特殊 stage 的尝试记录追加，任务详情里审计可见。"""
    last_no = session.exec(
        select(func.max(SendAttempt.attempt_no)).where(SendAttempt.job_id == job_id)
    ).one()
    now = utc_now()
    session.add(
        SendAttempt(
            job_id=job_id,
            attempt_no=(last_no or 0) + 1,
            stage=stage,
            status=AttemptStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
        )
    )


def _get_unknown_job(session: Session, job_id: str, user_id: str) -> SendJob:
    job = session.get(SendJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    account = session.get(Account, job.account_id)
    if account is None or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != JobStatus.UNKNOWN_REQUIRES_REVIEW:
        raise HTTPException(
            status_code=409, detail="只有「待人工复核」的任务可以人工处理"
        )
    return job


def confirm_job_sent(session: Session, job_id: str, user_id: str) -> JobRead:
    """人工确认 unknown 任务的消息已实际发出：对齐自动成功路径，补齐统计与火花。"""
    job = _get_unknown_job(session, job_id, user_id)

    now = utc_now()
    job.status = JobStatus.SUCCEEDED
    job.failure_class = None
    job.confirmed_at = now
    job.updated_at = now
    session.add(job)

    target = session.get(ContractTarget, job.target_id)
    if target is not None:
        target.last_sent_at = now
        target.last_confirmed_at = now
        target.streak_days += 1
        target.updated_at = now
        session.add(target)
    account = session.get(Account, job.account_id)
    if account is not None:
        account.last_success_at = now
        session.add(account)

    _record_manual_action(session, job_id, "manual_confirm_sent")
    session.commit()
    session.refresh(job)
    return JobRead.model_validate(job)


def release_job(session: Session, job_id: str, user_id: str) -> JobRead:
    """人工确认消息未发出，放行重试：任务回到 SCHEDULED，由「立即发送」重新执行。"""
    job = _get_unknown_job(session, job_id, user_id)

    now = utc_now()
    job.status = JobStatus.SCHEDULED
    job.failure_class = None
    job.confirmed_at = None
    job.updated_at = now
    session.add(job)

    _record_manual_action(session, job_id, "manual_release")
    session.commit()
    session.refresh(job)
    return JobRead.model_validate(job)


# ---------------------------------------------------------------- send preview


def send_preview(session: Session, account_id: str, user_id: str) -> dict:
    account = session.get(Account, account_id)
    if account is None or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="账号不存在")

    targets = session.exec(
        select(ContractTarget)
        .where(ContractTarget.account_id == account_id, ContractTarget.enabled)
        .order_by(ContractTarget.created_at)
    ).all()
    targets = [t for t in targets if t.deleted_at is None]
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


def get_dashboard(session: Session, user_id: str) -> DashboardSummary:
    account_ids = session.exec(select(Account.id).where(Account.user_id == user_id)).all()
    account_count = len(account_ids)
    healthy_count = (
        session.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.user_id == user_id, Account.status == AccountStatus.HEALTHY)
        )
        or 0
    )
    if account_ids:
        active_contracts = (
            session.scalar(
                select(func.count())
                .select_from(ContractTarget)
                .where(
                    ContractTarget.account_id.in_(account_ids),
                    ContractTarget.enabled,
                    ContractTarget.deleted_at.is_(None),
                )
            )
            or 0
        )
    else:
        active_contracts = 0

    today = date.today()
    today_succeeded = (
        session.scalar(
            select(func.count(func.distinct(SendJob.target_id)))
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
            .where(
                Account.user_id == user_id,
                Account.status.in_(ATTENTION_ACCOUNT_STATUSES),
            )
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

    if account_ids:
        accounts = session.exec(
            select(Account)
            .where(Account.user_id == user_id)
            .order_by(Account.created_at)
        ).all()
        recent_jobs = session.exec(
            select(SendJob)
            .where(SendJob.account_id.in_(account_ids))
            .order_by(SendJob.updated_at.desc())
            .limit(8)
        ).all()
    else:
        accounts = []
        recent_jobs = []

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
