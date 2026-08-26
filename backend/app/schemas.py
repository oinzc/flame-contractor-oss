import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import AccountStatus, FailureClass, JobStatus, TemplateType

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
    enabled: bool | None = None
    status: Literal[AccountStatus.PENDING_LOGIN, AccountStatus.PAUSED] | None = None
    send_window_start: str | None = Field(default=None, pattern=TIME_RE.pattern)
    send_window_end: str | None = Field(default=None, pattern=TIME_RE.pattern)

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
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- template

class TemplateCreate(APIBase):
    name: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=1, max_length=1000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("模板名称不能为空")
        return value


class TemplateRead(APIBase):
    id: str
    name: str
    type: TemplateType
    content: str
    version: int
    enabled: bool
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


class TargetRead(APIBase):
    id: str
    account_id: str
    platform_user_id: str | None
    nickname_snapshot: str
    template_id: str | None
    enabled: bool
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


class DryRunTargetRead(APIBase):
    target: str
    status: Literal["found", "not_found", "ambiguous"]
    title: str | None = None


class DryRunRead(APIBase):
    account_id: str
    overall: Literal["ok", "auth_expired", "error"]
    detail: str | None = None
    targets: list[DryRunTargetRead]
    artifact_path: str | None = None

