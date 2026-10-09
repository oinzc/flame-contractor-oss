import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core.config import AUTO_SCAN_INTERVAL_MAX_HOURS, AUTO_SCAN_INTERVAL_MIN_HOURS
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


class UserRegister(APIBase):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=255)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("用户名不能为空")
        return value

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        return value or None

    @field_validator("email")
    @classmethod
    def _strip_email(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().lower()
        return value or None


class UserLogin(APIBase):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str) -> str:
        value = value.strip()
        return value


class AuthSetupRead(APIBase):
    needs_setup: bool


class UserCreateAdmin(UserRegister):
    """管理员创建平台用户；可按需同时授予管理员权限。"""

    is_superuser: bool = False


class UserRead(APIBase):
    id: str
    username: str
    display_name: str | None = None
    email: str | None = None
    is_superuser: bool = False
    created_at: datetime
    updated_at: datetime


class UserUpdateMe(APIBase):
    """更新当前用户资料（昵称/邮箱）。密码走专门的 /me/password 路由。"""

    display_name: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=255)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        return value or None

    @field_validator("email")
    @classmethod
    def _strip_email(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().lower()
        return value or None


class UpdatePassword(APIBase):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserUpdateAdmin(APIBase):
    """管理员编辑某用户：可重置密码、调整管理员标记。"""

    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_superuser: bool | None = None


class AuthRead(APIBase):
    token: str
    user: UserRead


class AuthSessionRead(APIBase):
    """服务端登录会话的可读视图；current 由 deps 当前会话比对填充。"""

    id: str
    created_at: datetime
    last_seen_at: datetime | None = None
    expires_at: datetime
    current: bool = False
    user_agent: str | None = None
    ip_address: str | None = None
    revoked_at: datetime | None = None


class PasswordRecoveryRequest(APIBase):
    """找回密码入口：按 username 定位账号。自托管下不签发重置 token。"""

    username: str = Field(min_length=1, max_length=60)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str) -> str:
        value = value.strip()
        return value


class Message(APIBase):
    message: str


class AccountCreate(APIBase):
    display_name: str = Field(min_length=1, max_length=60)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    send_window_start: str = Field(default="20:00", pattern=TIME_RE.pattern)
    send_window_end: str = Field(default="22:00", pattern=TIME_RE.pattern)
    # 账号级精确发送时刻（HH:MM）；为空=在发送窗口内随机取时
    send_time_hhmm: str | None = Field(default=None, pattern=TIME_RE.pattern)

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

    @field_validator("send_time_hhmm")
    @classmethod
    def _strip_send_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

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
    send_time_hhmm: str | None = Field(default=None, pattern=TIME_RE.pattern)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("显示名称不能为空")
        return value

    @field_validator("send_time_hhmm")
    @classmethod
    def _strip_send_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

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
    send_time_hhmm: str | None
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

ConversationType = Literal["user", "group"]
IdentityStatus = Literal["bound", "ambiguous", "not_observed"]

class TargetCreate(APIBase):
    account_id: str
    nickname_snapshot: str = Field(min_length=1, max_length=80)
    platform_user_id: str | None = Field(default=None, max_length=128)
    # 抖音公开展示号（unique_id/short_id），仅用于界面显示，不参与发送核对。
    platform_handle: str | None = Field(default=None, max_length=128)
    # 群聊使用会话唯一 ID；不与用户 sec_uid 混用。
    platform_conversation_id: str | None = Field(default=None, max_length=128)
    conversation_type: ConversationType | None = None
    template_id: str | None = None
    streak_days: int = Field(default=0, ge=0)
    # 好友级精确发送时刻（HH:MM）；为空=跟随账号（账号精确时刻或窗口随机）
    send_time_hhmm: str | None = Field(default=None, pattern=TIME_RE.pattern)

    @field_validator("nickname_snapshot")
    @classmethod
    def _strip_nickname(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("昵称不能为空")
        return value

    @field_validator("send_time_hhmm")
    @classmethod
    def _strip_send_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("platform_user_id")
    @classmethod
    def _strip_platform_user_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("platform_handle", "platform_conversation_id")
    @classmethod
    def _strip_platform_identity_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _validate_identity_kind(self):
        if self.platform_user_id and self.platform_conversation_id:
            raise ValueError("platformUserId 与 platformConversationId 不能同时填写")
        if self.conversation_type == "group" and self.platform_user_id:
            raise ValueError("群聊只能填写 platformConversationId，不能填写 platformUserId")
        if self.conversation_type == "user" and self.platform_conversation_id:
            raise ValueError("用户会话不能填写 platformConversationId")
        if self.platform_conversation_id and self.conversation_type is None:
            self.conversation_type = "group"
        elif self.platform_user_id and self.conversation_type is None:
            self.conversation_type = "user"
        return self


class TargetPatch(APIBase):
    """局部更新契约对象。官方火花快照只能由抖音页面扫描写入。"""

    enabled: bool | None = None
    template_id: str | None = None
    send_time_hhmm: str | None = Field(default=None, pattern=TIME_RE.pattern)

    @field_validator("send_time_hhmm")
    @classmethod
    def _strip_send_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class BatchTargetItem(APIBase):
    nickname: str
    # 抖音用户 sec_uid（扫描响应可取得时保存）；缺失时仍走昵称严格匹配。
    platform_user_id: str | None = Field(default=None, max_length=128)
    # unique_id/short_id 等可读平台号；不作为发送安全核对键。
    platform_handle: str | None = Field(default=None, max_length=128)
    # 群聊会话唯一 ID；与 platform_user_id 二选一，由扫描自动填充。
    platform_conversation_id: str | None = Field(default=None, max_length=128)
    conversation_type: ConversationType | None = None
    # 扫描身份诊断：仅用于让导入结果解释「为何没有写入稳定身份」。
    identity_status: IdentityStatus | None = None
    conversation_status: IdentityStatus | None = None
    # 扫描读到的抖音官方火花天数：导入时回填为初始值（之后仍本地累计）
    streak_days: int | None = Field(default=None, ge=0)
    # 扫描读到的抖音官方数字火花天数；只作为展示快照，不覆盖本地 streak_days
    spark_days: int | None = Field(default=None, ge=0)
    # 扫描读到的抖音官方状态：只作为展示快照，不覆盖本地 streak_days
    spark_kind: str | None = Field(default=None, max_length=16)
    spark_label: str | None = Field(default=None, max_length=64)

    @field_validator("platform_user_id", "platform_handle", "platform_conversation_id")
    @classmethod
    def _strip_platform_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _validate_identity_kind(self):
        if self.platform_user_id and self.platform_conversation_id:
            raise ValueError("platformUserId 与 platformConversationId 不能同时填写")
        if self.conversation_type == "group" and self.platform_user_id:
            raise ValueError("群聊只能填写 platformConversationId，不能填写 platformUserId")
        if self.conversation_type == "user" and self.platform_conversation_id:
            raise ValueError("用户会话不能填写 platformConversationId")
        if self.platform_conversation_id and self.conversation_type is None:
            self.conversation_type = "group"
        elif self.platform_user_id and self.conversation_type is None:
            self.conversation_type = "user"
        return self


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
                "platform_user_id": item.platform_user_id,
                "platform_handle": item.platform_handle,
                "platform_conversation_id": item.platform_conversation_id,
                "conversation_type": item.conversation_type,
                "streak_days": item.streak_days,
            }
            # 保留调用方是否真的携带了官方快照字段；旧客户端只传昵称时，
            # 不能被误判成「清空已有官方状态」的刷新请求。
            for field in (
                "spark_kind",
                "spark_label",
                "spark_days",
                "identity_status",
                "conversation_status",
            ):
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
    identity_bound: list[str] = []
    identity_unresolved: list[str] = []
    identity_ambiguous: list[str] = []
    conversation_bound: list[str] = []
    conversation_unresolved: list[str] = []
    conversation_ambiguous: list[str] = []


class TargetRead(APIBase):
    id: str
    account_id: str
    platform_user_id: str | None
    platform_handle: str | None
    platform_conversation_id: str | None
    conversation_type: ConversationType | None
    nickname_snapshot: str
    template_id: str | None
    enabled: bool
    # 好友级精确发送时刻（HH:MM）；为空=跟随账号
    send_time_hhmm: str | None
    spark_days: int | None
    spark_kind: str | None
    spark_label: str | None
    spark_scanned_at: datetime | None
    streak_days: int
    last_sent_at: datetime | None
    last_confirmed_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- job

class JobRead(APIBase):
    id: str
    account_id: str
    target_id: str
    # 单好友手动发送「直接输入文本」时不引用模板，故可空。
    template_id: str | None
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


class AutoScanStatusRead(APIBase):
    account_id: str
    task_id: str | None = None
    status: Literal[
        "scheduled",
        "due",
        "running",
        "paused",
        "waiting_login",
        "disabled",
        "no_targets",
    ]
    last_scan_at: datetime | None
    next_scan_at: datetime | None
    detail: str | None


class SchedulerSettingsPatch(APIBase):
    """更新自动会话状态同步周期；单位为小时。"""

    auto_scan_interval_hours: int = Field(
        ge=AUTO_SCAN_INTERVAL_MIN_HOURS,
        le=AUTO_SCAN_INTERVAL_MAX_HOURS,
    )


class SchedulerStatusRead(APIBase):
    enabled: bool
    paused: bool
    running_accounts: list[str]
    today_schedules: list[SendScheduleRead]
    auto_scan_interval_hours: int
    auto_scans: list[AutoScanStatusRead]


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

LoginState = Literal[
    "idle",
    # 截图扫码：已开始渲染登录页，二维码尚未可读（慢网络下可达数十秒）
    "preparing",
    "waiting_scan",
    "succeeded",
    "failed",
    "expired",
]


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
    platform_user_id: str | None = None
    platform_handle: str | None = None
    platform_conversation_id: str | None = None
    conversation_type: ConversationType | None = None
    identity_status: IdentityStatus = "not_observed"
    identity_source: Literal["remark_name", "nickname", "unique_id", "short_id"] | None = None
    identity_candidate_count: int = Field(default=0, ge=0)
    identity_detail: str | None = None
    conversation_status: IdentityStatus = "not_observed"
    conversation_candidate_count: int = Field(default=0, ge=0)
    conversation_detail: str | None = None
    preview: str = ""
    streak_days: int | None = None
    streak_kind: str | None = None
    streak_label: str | None = None


class ScanChatsRead(APIBase):
    """会话列表全量扫描结果（只读）。overall=ok 时 entries 为去重后的全部会话。"""

    overall: Literal["ok", "auth_expired", "error"]
    detail: str | None = None
    entries: list[ScanChatItem] = []


ReconciliationCategory = Literal[
    "changed",
    "no_spark",
    "new",
    "not_observed",
    "unchanged",
]


class ScanOfficialStateRead(APIBase):
    """Official spark state at one side of a reconciliation comparison."""

    days: int | None = None
    kind: str | None = None
    label: str | None = None
    # Only previous snapshots normally carry this value; keeping it optional
    # makes the shape safe for both sides and for old persisted records.
    scanned_at: datetime | None = None


class ScanReconciliationCountsRead(APIBase):
    changed: int = Field(default=0, ge=0)
    no_spark: int = Field(default=0, ge=0)
    new: int = Field(default=0, ge=0)
    not_observed: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)


class ScanReconciliationItemRead(APIBase):
    target_id: str | None = None
    title: str
    conversation_type: ConversationType | None = None
    category: ReconciliationCategory
    previous: ScanOfficialStateRead
    current: ScanOfficialStateRead


class ScanReconciliationRead(APIBase):
    schema_version: int | None = None
    counts: ScanReconciliationCountsRead = Field(
        default_factory=ScanReconciliationCountsRead
    )
    entries: list[ScanReconciliationItemRead] = Field(default_factory=list)


class ScanProgressRead(APIBase):
    """后台会话扫描进度；滚动到底前 total 未知，不伪造百分比。"""

    task_id: str
    account_id: str
    source: Literal["manual", "automatic"] = "manual"
    status: Literal["running", "completed", "failed", "interrupted", "cancelled"]
    total: int | None = None
    completed: int = 0
    stage: str
    current_item: str | None = None
    round: int = 0
    max_rounds: int = 80
    overall: Literal["ok", "auth_expired", "error"] | None = None
    detail: str | None = None
    entries: list[ScanChatItem] = []
    reconciliation: ScanReconciliationRead = Field(
        default_factory=ScanReconciliationRead
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ScanHistoryRead(APIBase):
    """轻量扫描历史；刻意不包含完整 entries_json。"""

    task_id: str
    account_id: str
    source: Literal["manual", "automatic"] = "manual"
    status: Literal["running", "completed", "failed", "interrupted", "cancelled"]
    total: int | None = None
    completed: int = 0
    overall: Literal["ok", "auth_expired", "error"] | None = None
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reconciliation: ScanReconciliationRead = Field(
        default_factory=ScanReconciliationRead
    )


class IdentityHydrationStart(APIBase):
    """请求为已存在的普通好友补采稳定身份；不接受昵称，避免目标漂移。"""

    target_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("target_ids")
    @classmethod
    def _deduplicate_target_ids(cls, value: list[str]) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for target_id in value:
            target_id = target_id.strip()
            if not target_id or target_id in seen:
                continue
            seen.add(target_id)
            ids.append(target_id)
        if not ids:
            raise ValueError("至少选择一个好友")
        return ids


class IdentityHydrationResultRead(APIBase):
    """单个好友的身份补采结果；只返回必要字段与诊断，不返回完整抖音响应。"""

    target_id: str
    nickname: str
    status: Literal["bound", "ambiguous", "not_observed", "conflict"]
    opened: bool = False
    platform_user_id: str | None = None
    platform_handle: str | None = None
    identity_source: Literal["remark_name", "nickname", "unique_id", "short_id"] | None = None
    identity_candidate_count: int = Field(default=0, ge=0)
    detail: str | None = None


class IdentityHydrationProgressRead(APIBase):
    """普通好友身份补采进度；结果写入 SQLite，切页/刷新后仍可读取。"""

    task_id: str
    account_id: str
    status: Literal["running", "completed", "failed", "interrupted", "cancelled"]
    total: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    stage: str
    current_item: str | None = None
    overall: Literal["ok", "partial", "auth_expired", "error"] | None = None
    detail: str | None = None
    results: list[IdentityHydrationResultRead] = []
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


class SendTargetPayload(APIBase):
    """单好友手动强制发送的入参：选模板或直接输入文本（二选一）。"""

    template_id: str | None = None
    content: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _validate_template_or_content(self) -> "SendTargetPayload":
        has_template = bool(self.template_id)
        has_content = bool(self.content)
        if not has_template and not has_content:
            raise ValueError("模板与内容至少提供一项")
        if has_template and has_content:
            raise ValueError("模板与内容不能同时提供")
        return self


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
