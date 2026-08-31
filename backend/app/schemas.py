import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import AccountStatus, FailureClass, JobStatus, ScheduleStatus, TemplateType

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class APIBase(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra="forbid",
    )


# ---------------------------------------------------------------- account


class AccountCreate(APIBase):
    display_name: str = Field(min_length=1, max_length=60)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    send_window_start: str = Field(default="20:00", pattern=TIME_RE.pattern)
    send_window_end: str = Field(default="22:00", pattern=TIME_RE.pattern)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("显示名称不能为空")
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("未知时区") from None
        return value

    @model_validator(mode="after")
    def _validate_window(self) -> "AccountCreate":
        if self.send_window_start == self.send_window_end:
            raise ValueError("发送窗口起点与终点不能相同")
        return self


class AccountPatch(APIBase):
    display_name: str | None = Field(default=None, min_length=1, max_length=60)
    enabled: bool | None = None
    status: Literal[AccountStatus.PENDING_LOGIN, AccountStatus.PAUSED] | None = None
    send_window_start: str | None = Field(default=None, pattern=TIME_RE.pattern)
    send_window_end: str | None = Field(default=None, pattern=TIME_RE.pattern)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("显示名称不能为空")
        return value

    @model_validator(mode="after")
    def _validate_window(self) -> "AccountPatch":
        start = self.send_window_start
        end = self.send_window_end
        if start is not None and end is not None and start == end:
            raise ValueError("发送窗口起点与终点不能相同")
        return self


class AccountRead(APIBase):
    id: str
    display_name: str
    status: AccountStatus
    enabled: bool
    timezone: str
    send_window_start: str
    send_window_end: str
    last_verified_at: datetime | None
    last_success_at: datetime | None
    # 登录成功后读到的抖音身份昵称（与 display_name 的对照）
    douyin_nickname: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- template

class PoolItemIn(APIBase):
    """M8-1 文案池候选入参。weekday_mask: 0=每天；bit0=周一 … bit6=周日。"""

    content: str = Field(min_length=1, max_length=500)
    weekday_mask: int = Field(default=0, ge=0, le=127)
    enabled: bool = True


class PoolItemRead(APIBase):
    id: str
    content: str
    weekday_mask: int
    enabled: bool
    last_used_at: datetime | None = None


class TemplateCreate(APIBase):
    name: str = Field(min_length=1, max_length=60)
    type: TemplateType = Field(default=TemplateType.TEXT)
    content: str = Field(default="", max_length=1000)
    image_path: str | None = Field(default=None, max_length=500)
    sticker_name: str | None = Field(default=None, max_length=64)
    sticker_label: str | None = Field(default=None, max_length=64)
    sticker_category: str | None = Field(default=None, max_length=32)
    sticker_fallback_index: int | None = Field(default=None, ge=0)
    pool_items: list[PoolItemIn] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("模板名称不能为空")
        return value


class TemplatePatch(APIBase):
    """局部更新模板。type 不可改——要换类型请删除重建，避免字段语义混乱。"""

    name: str | None = Field(default=None, min_length=1, max_length=60)
    content: str | None = Field(default=None, max_length=1000)
    image_path: str | None = Field(default=None, max_length=500)
    sticker_name: str | None = Field(default=None, max_length=64)
    sticker_label: str | None = Field(default=None, max_length=64)
    sticker_category: str | None = Field(default=None, max_length=32)
    sticker_fallback_index: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    # 非 None 时整体替换文案池候选（无增量编辑，列表即真相）
    pool_items: list[PoolItemIn] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("模板名称不能为空")
        return value


class TemplateRead(APIBase):
    id: str
    name: str
    type: TemplateType
    content: str
    image_path: str | None
    sticker_name: str | None
    sticker_label: str | None
    sticker_category: str | None
    sticker_fallback_index: int | None
    version: int
    enabled: bool
    pool_items: list[PoolItemRead] = []
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- target

class TargetCreate(APIBase):
    account_id: str
    nickname_snapshot: str = Field(min_length=1, max_length=80)
    platform_user_id: str | None = Field(default=None, max_length=128)
    template_id: str | None = None
    streak_days: int = Field(default=0, ge=0)

    @field_validator("nickname_snapshot")
    @classmethod
    def _strip_nickname(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("昵称不能为空")
        return value


class TargetPatch(APIBase):
    """局部更新契约对象。开放火花天数校准、启停与换绑模板（None=解绑）。"""

    streak_days: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    template_id: str | None = None


class BatchTargetItem(APIBase):
    nickname: str
    # 扫描读到的抖音官方火花天数：导入时回填为初始值（之后仍本地累计）
    streak_days: int | None = Field(default=None, ge=0)
    # 扫描读到的抖音官方状态：只作为展示快照，不覆盖本地 streak_days
    spark_kind: str | None = Field(default=None, max_length=16)
    spark_label: str | None = Field(default=None, max_length=64)


class TargetBatchCreate(APIBase):
    """批量添加好友：昵称逐行解析为独立好友，重复项跳过而非整批失败。"""

    account_id: str
    template_id: str | None = None
    items: list[BatchTargetItem] = Field(max_length=200)

    @field_validator("items")
    @classmethod
    def _strip_items(cls, value: list[BatchTargetItem]) -> list[BatchTargetItem]:
        stripped: list[BatchTargetItem] = []
        for item in value:
            if not item.nickname or not item.nickname.strip():
                continue
            data: dict[str, object] = {
                "nickname": item.nickname.strip(),
                "streak_days": item.streak_days,
            }
            # 保留调用方是否真的携带了官方快照字段；旧客户端只传昵称时，
            # 不能被误判成「清空已有官方状态」的刷新请求。
            for field in ("spark_kind", "spark_label"):
                if field in item.model_fields_set:
                    data[field] = getattr(item, field)
            stripped.append(BatchTargetItem(**data))
        if not stripped:
            raise ValueError("昵称列表不能为空")
        for item in stripped:
            if len(item.nickname) > 80:
                raise ValueError("单条昵称不能超过 80 字")
        return stripped


class TargetBatchRead(APIBase):
    created: list[str] = []
    refreshed: list[str] = []
    duplicates: list[str] = []
    failed: list[str] = []


class TargetRead(APIBase):
    id: str
    account_id: str
    platform_user_id: str | None
    nickname_snapshot: str
    template_id: str | None
    enabled: bool
    spark_kind: str | None
    spark_label: str | None
    spark_scanned_at: datetime | None
    streak_days: int
    last_sent_at: datetime | None
    last_confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- job

class JobRead(APIBase):
    id: str
    account_id: str
    target_id: str
    template_id: str
    business_date: date
    message_slot_id: str
    status: JobStatus
    scheduled_at: datetime | None
    attempt_count: int
    failure_class: FailureClass | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- scheduler

class SendScheduleRead(APIBase):
    id: str
    account_id: str
    business_date: date
    scheduled_at: datetime | None
    status: ScheduleStatus
    attempt_count: int
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None


class SchedulerStatusRead(APIBase):
    enabled: bool
    paused: bool
    running_accounts: list[str]
    today_schedules: list[SendScheduleRead]


# ---------------------------------------------------------------- dashboard

class DashboardSummary(APIBase):
    account_count: int
    healthy_account_count: int
    active_contract_count: int
    today_succeeded: int
    today_pending: int
    needs_attention: int
    accounts: list[AccountRead]
    recent_jobs: list[JobRead]


# ---------------------------------------------------------------- execution

LoginState = Literal["idle", "waiting_scan", "succeeded", "failed", "expired"]


class LoginStatusRead(APIBase):
    state: LoginState
    detail: str | None = None
    # M8-4 截图扫码登录：二维码截图落盘路径（仅 QR 登录端点附带）
    qr_image: str | None = None
    # 登录成功后读到的当前抖音身份昵称（localStorage user_info，仅展示）
    self_nickname: str | None = None


class DryRunTargetRead(APIBase):
    target: str
    status: Literal["found", "not_found", "ambiguous"]
    title: str | None = None
    detail: str | None = None


class DryRunRead(APIBase):
    account_id: str
    overall: Literal["ok", "auth_expired", "error"]
    detail: str | None = None
    targets: list[DryRunTargetRead]
    visible_titles: list[str] = []
    diagnostics: dict | None = None
    artifact_path: str | None = None


class ScanChatItem(APIBase):
    """扫描到的单个会话。streak_days 为抖音官方火花天数；streak_label 为特殊态
    文字标记（如「重燃中 1/3」「5 天后消失」）；两者都为空且消息未提及火花 = 无火花。"""

    title: str
    preview: str = ""
    streak_days: int | None = None
    streak_kind: str | None = None
    streak_label: str | None = None


class ScanChatsRead(APIBase):
    """会话列表全量扫描结果（只读）。overall=ok 时 entries 为去重后的全部会话。"""

    overall: Literal["ok", "auth_expired", "error"]
    detail: str | None = None
    entries: list[ScanChatItem] = []


class ScanProgressRead(APIBase):
    """后台会话扫描进度；滚动到底前 total 未知，不伪造百分比。"""

    task_id: str
    account_id: str
    status: Literal["running", "completed", "failed"]
    total: int | None = None
    completed: int = 0
    stage: str
    current_item: str | None = None
    round: int = 0
    max_rounds: int = 80
    overall: Literal["ok", "auth_expired", "error"] | None = None
    detail: str | None = None
    entries: list[ScanChatItem] = []
    started_at: datetime | None = None
    finished_at: datetime | None = None


# ---------------------------------------------------------------- send

class SendPreviewItemRead(APIBase):
    target_id: str
    target_name: str
    template_id: str | None = None
    message: str | None = None
    reason: str | None = None


class SendPreviewRead(APIBase):
    account_id: str
    business_date: str
    items: list[SendPreviewItemRead]


class SendTargetResultRead(APIBase):
    target: str
    status: str
    failure_class: FailureClass | None = None
    detail: str | None = None
    message: str | None = None
    # M6-1 诊断字段：失败/未确认时的现场截图与当时页面 URL
    screenshot: str | None = None
    page_url: str | None = None


class SendRead(APIBase):
    account_id: str
    overall: Literal["ok", "partial", "auth_expired", "error", "network_unavailable"]
    detail: str | None = None
    results: list[SendTargetResultRead]
    artifact_path: str | None = None


class SendProgressRead(APIBase):
    """发送进度快照；只反映处理数量和当前阶段，不承载登录态。"""

    account_id: str
    status: Literal["idle", "running", "completed", "failed"]
    total: int
    completed: int
    current_target: str | None = None
    stage: str
    last_result: str | None = None
    overall: Literal["ok", "partial", "auth_expired", "error", "network_unavailable"] | None = None
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None



# ---------------------------------------------------------------- backup (M8-3)


class BackupData(APIBase):
    """全量数据块：各行 model_dump(mode="json") 的原始字典。"""

    accounts: list[dict] = []
    templates: list[dict] = []
    pool_items: list[dict] = []
    targets: list[dict] = []
    schedules: list[dict] = []
    jobs: list[dict] = []
    attempts: list[dict] = []


class BackupRead(APIBase):
    app: str
    schema_version: int
    exported_at: datetime
    data: BackupData


class BackupImportResult(APIBase):
    inserted: dict[str, int]
    updated: dict[str, int]
