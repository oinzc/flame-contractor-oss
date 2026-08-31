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
    DashboardSummary,
    JobRead,
    PoolItemIn,
    PoolItemRead,
    TargetCreate,
    TargetBatchCreate,
    TargetBatchRead,
    TargetPatch,
    TargetRead,    TemplateCreate,
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
    if payload.platform_user_id:
        same_platform_user = session.exec(
            select(ContractTarget).where(
                ContractTarget.account_id == payload.account_id,
                ContractTarget.platform_user_id == payload.platform_user_id,
            )
        ).first()
        if same_platform_user is not None:
            raise HTTPException(status_code=409, detail="该账号下已存在同一抖音用户")

    target = ContractTarget.model_validate(payload)
    session.add(target)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="该账号下已存在同名契约对象") from None
    session.refresh(target)
    return TargetRead.model_validate(target)


def create_targets_batch(session: Session, payload: TargetBatchCreate) -> TargetBatchRead:
    """批量添加好友。逐条独立提交：重复项跳过记录，其余失败也不中断整批。"""
    if session.get(Account, payload.account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if payload.template_id and session.get(MessageTemplate, payload.template_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")

    created: list[str] = []
    refreshed: list[str] = []
    duplicates: list[str] = []
    failed: list[str] = []
    for item in payload.items:
        nickname = item.nickname
        platform_user_id = item.platform_user_id.strip() if item.platform_user_id else None
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
        nickname_existing = session.exec(
            select(ContractTarget).where(
                ContractTarget.account_id == payload.account_id,
                ContractTarget.nickname_snapshot == nickname,
            )
        ).first()
        if existing is None:
            existing = nickname_existing
        if existing is not None:
            if (
                platform_user_id
                and existing.platform_user_id
                and existing.platform_user_id != platform_user_id
            ):
                failed.append(f"{nickname}：稳定身份与已有好友不一致")
                continue
            if platform_user_id and existing.platform_user_id is None:
                existing.platform_user_id = platform_user_id
            if existing.platform_user_id == platform_user_id and existing.nickname_snapshot != nickname:
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
            # 扫描导入可以刷新已有好友的官方徽标快照；本地累计天数保持不变。
            if (
                platform_user_id
                or "spark_kind" in item.model_fields_set
                or "spark_label" in item.model_fields_set
            ):
                now = utc_now()
                existing.spark_kind = item.spark_kind
                existing.spark_label = item.spark_label
                existing.spark_scanned_at = now
                existing.updated_at = now
                session.add(existing)
                session.commit()
                refreshed.append(nickname)
            else:
                duplicates.append(nickname)
            continue
        try:
            scanned_at = (
                utc_now()
                if "spark_kind" in item.model_fields_set or "spark_label" in item.model_fields_set
                else None
            )
            session.add(
                ContractTarget(
                    account_id=payload.account_id,
                    platform_user_id=platform_user_id,
                    nickname_snapshot=nickname,
                    template_id=payload.template_id,
                    # 扫描读到的官方火花天数作为初始值；之后仍走本地累计
                    streak_days=item.streak_days or 0,
                    spark_kind=item.spark_kind,
                    spark_label=item.spark_label,
                    spark_scanned_at=scanned_at,
                )
            )
            session.commit()
            created.append(nickname)
        except IntegrityError:
            session.rollback()
            duplicates.append(nickname)
        except Exception as exc:  # 单条失败不拖垮整批
            session.rollback()
            failed.append(f"{nickname}：{exc.__class__.__name__}")
    return TargetBatchRead(
        created=created,
        refreshed=refreshed,
        duplicates=duplicates,
        failed=failed,
    )


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
    """局部更新契约对象：火花天数校准、启停、换绑模板（templateId=null 解绑）。"""
    target = session.get(ContractTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="好友不存在")

    data = payload.model_dump(exclude_unset=True)
    if data.get("template_id") is not None and session.get(MessageTemplate, data["template_id"]) is None:
        raise HTTPException(status_code=404, detail="模板不存在")

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


def _get_unknown_job(session: Session, job_id: str) -> SendJob:
    job = session.get(SendJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != JobStatus.UNKNOWN_REQUIRES_REVIEW:
        raise HTTPException(
            status_code=409, detail="只有「待人工复核」的任务可以人工处理"
        )
    return job


def confirm_job_sent(session: Session, job_id: str) -> JobRead:
    """人工确认 unknown 任务的消息已实际发出：对齐自动成功路径，补齐统计与火花。"""
    job = _get_unknown_job(session, job_id)

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


def release_job(session: Session, job_id: str) -> JobRead:
    """人工确认消息未发出，放行重试：任务回到 SCHEDULED，由「立即发送」重新执行。"""
    job = _get_unknown_job(session, job_id)

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
