import uuid
from datetime import date, datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel, UniqueConstraint


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class AccountStatus(str, Enum):
    PENDING_LOGIN = "pending_login"
    HEALTHY = "healthy"
    RUNNING = "running"
    PAUSED = "paused"
    AUTH_EXPIRED = "auth_expired"
    VERIFICATION_REQUIRED = "verification_required"
    DOM_BROKEN = "dom_broken"
    COOLDOWN = "cooldown"


class TemplateType(str, Enum):
    TEXT = "text"


class JobStatus(str, Enum):
    SCHEDULED = "scheduled"
    LEASED = "leased"
    OPENING_CHAT = "opening_chat"
    SENDING = "sending"
    CONFIRMING = "confirming"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    UNKNOWN_REQUIRES_REVIEW = "unknown_requires_review"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ScheduleStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    SENDING = "sending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_REQUIRES_REVIEW = "unknown_requires_review"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class FailureClass(str, Enum):
    NETWORK = "network"
    AUTH = "auth"
    VERIFICATION = "verification"
    TARGET_UNCERTAIN = "target_uncertain"
    DOM_CHANGED = "dom_changed"
    UNKNOWN_OUTCOME = "unknown_outcome"
    OTHER = "other"


class Account(SQLModel, table=True):
    __tablename__ = "account"

    id: str = Field(default_factory=new_uuid, primary_key=True)
    display_name: str = Field(min_length=1, max_length=60, unique=True, index=True)
    status: AccountStatus = Field(default=AccountStatus.PENDING_LOGIN, index=True)
    enabled: bool = Field(default=True, index=True)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    send_window_start: str = Field(default="20:00", max_length=5)
    send_window_end: str = Field(default="22:00", max_length=5)
    last_verified_at: datetime | None = None
    last_success_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MessageTemplate(SQLModel, table=True):
    __tablename__ = "message_template"

    id: str = Field(default_factory=new_uuid, primary_key=True)
    name: str = Field(min_length=1, max_length=60, unique=True, index=True)
    type: TemplateType = Field(default=TemplateType.TEXT, max_length=16)
    content: str = Field(min_length=1, max_length=1000)
    version: int = Field(default=1, ge=1)
    enabled: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ContractTarget(SQLModel, table=True):
    __tablename__ = "contract_target"
    __table_args__ = (
        UniqueConstraint("account_id", "nickname_snapshot", name="uq_target_account_nickname"),
    )

    id: str = Field(default_factory=new_uuid, primary_key=True)
    account_id: str = Field(foreign_key="account.id", index=True, ondelete="RESTRICT")
    platform_user_id: str | None = Field(default=None, max_length=128, index=True)
    nickname_snapshot: str = Field(min_length=1, max_length=80, index=True)
    template_id: str | None = Field(
        default=None, foreign_key="message_template.id", ondelete="RESTRICT"
    )
    enabled: bool = Field(default=True, index=True)
    streak_days: int = Field(default=0, ge=0)
    last_sent_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SendJob(SQLModel, table=True):
    __tablename__ = "send_job"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "target_id",
            "business_date",
            "message_slot_id",
            name="uq_send_job_daily_slot",
        ),
    )

    id: str = Field(default_factory=new_uuid, primary_key=True)
    account_id: str = Field(foreign_key="account.id", index=True, ondelete="RESTRICT")
    target_id: str = Field(foreign_key="contract_target.id", index=True, ondelete="RESTRICT")
    template_id: str = Field(foreign_key="message_template.id", ondelete="RESTRICT")
    business_date: date = Field(index=True)
    message_slot_id: str = Field(max_length=64)
    status: JobStatus = Field(default=JobStatus.SCHEDULED, index=True)
    scheduled_at: datetime | None = None
    lease_owner: str | None = Field(default=None, max_length=128)
    lease_expires_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0)
    failure_class: FailureClass | None = None
    confirmed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SendAttempt(SQLModel, table=True):
    __tablename__ = "send_attempt"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no", name="uq_attempt_job_no"),)

    id: str = Field(default_factory=new_uuid, primary_key=True)
    job_id: str = Field(foreign_key="send_job.id", index=True, ondelete="CASCADE")
    attempt_no: int = Field(ge=1)
    stage: str = Field(default=JobStatus.SCHEDULED, max_length=32)
    status: AttemptStatus = Field(default=AttemptStatus.RUNNING)
    failure_class: FailureClass | None = None
    artifact_ref: str | None = Field(default=None, max_length=256)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class SendSchedule(SQLModel, table=True):
    """账号级每日调度记录：同账号同业务日期唯一，驱动自动发送。

    与 SendJob（好友级、幂等键）解耦：此处只决定「哪个账号今天何时发」，
    具体每个好友的发送仍由 run_send 走 SendJob 幂等；账号级租约在此实现。
    """

    __tablename__ = "send_schedule"
    __table_args__ = (
        UniqueConstraint("account_id", "business_date", name="uq_send_schedule_account_date"),
    )

    id: str = Field(default_factory=new_uuid, primary_key=True)
    account_id: str = Field(foreign_key="account.id", index=True, ondelete="RESTRICT")
    business_date: date = Field(index=True)
    scheduled_at: datetime | None = None
    status: ScheduleStatus = Field(default=ScheduleStatus.PENDING, index=True)
    lease_owner: str | None = Field(default=None, max_length=128)
    lease_expires_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=256)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
