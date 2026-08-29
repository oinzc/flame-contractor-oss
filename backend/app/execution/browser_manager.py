import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..core.config import Settings
from ..models import (
    Account,
    AccountStatus,
    AttemptStatus,
    ContractTarget,
    FailureClass,
    JobStatus,
    MessageTemplate,
    SendAttempt,
    SendJob,
    TemplateType,
)
from .douyin_chat import DomChangedError, DouyinChat, Sticker
from .message_renderer import render_message
from .profile_store import ensure_profile_dir
from .send_confirmer import SendConfirmer

LOGIN_CHECK_TIMEOUT = 8.0
LOGIN_POLL_INTERVAL = 2.0
LOGIN_TIMEOUT_SECONDS = 180.0
SEND_TIMEOUT_SECONDS = 15.0
SEND_POLL_INTERVAL = 0.5
SEND_STABILIZE_POLLS = 3

BrowserFactory = Callable[[Path, bool], Awaitable[tuple[object, object]]]

# Windows 有头窗口会被 Chromium 的原生遮挡计算误判为被遮挡而停止绘制（整页黑屏，
# headless 不受影响）；关闭该特性规避。headless 携带无害，故统一传入。
CHROMIUM_LAUNCH_ARGS = ["--disable-features=CalculateNativeWinOcclusion"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LoginFlow:
    account_id: str
    state: str = "waiting_scan"
    detail: str | None = None
    task: asyncio.Task | None = None


@dataclass
class DryRunTargetResult:
    target: str
    status: str  # found | not_found | ambiguous
    title: str | None = None
    detail: str | None = None


class BrowserManager:
    """每账号一个浏览器会话；负责扫码登录流程与 Dry Run。

    - 登录用 headed 持久 Profile（在本机弹出浏览器，用户扫码）；
    - Dry Run 用 headless 复用同一 Profile，只定位绝不发送；
    - 同一账号同时只允许一个活动浏览器流程；
    - 测试通过注入 browser_factory 替换真实 Playwright。
    """

    def __init__(
        self,
        engine,
        settings: Settings,
        browser_factory: BrowserFactory | None = None,
        *,
        login_poll_interval: float = LOGIN_POLL_INTERVAL,
        login_timeout: float = LOGIN_TIMEOUT_SECONDS,
        login_check_timeout: float = LOGIN_CHECK_TIMEOUT,
        send_timeout: float = SEND_TIMEOUT_SECONDS,
        send_poll_interval: float = SEND_POLL_INTERVAL,
        send_stabilize_polls: int = SEND_STABILIZE_POLLS,
        confirm_timeout: float = 8.0,
        search_timeout: float = 12.0,
        sleep=asyncio.sleep,
    ):
        self._engine = engine
        self._settings = settings
        self._flows: dict[str, LoginFlow] = {}
        self._playwright = None
        self._browser_factory = browser_factory
        self._login_poll_interval = login_poll_interval
        self._login_timeout = login_timeout
        self._login_check_timeout = login_check_timeout
        self._send_timeout = send_timeout
        self._send_poll_interval = send_poll_interval
        self._send_stabilize_polls = send_stabilize_polls
        self._confirm_timeout = confirm_timeout
        self._search_timeout = search_timeout
        self._sleep = sleep

    def _make_chat(self, page) -> DouyinChat:
        return DouyinChat(
            page,
            sleep=self._sleep,
            confirm_timeout=self._confirm_timeout,
            search_timeout=self._search_timeout,
        )

    # ------------------------------------------------------------ browser

    async def _open(self, profile_path: Path, headless: bool):
        if self._browser_factory is not None:
            return await self._browser_factory(profile_path, headless)
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=headless,
            args=CHROMIUM_LAUNCH_ARGS,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        return context, page

    # ------------------------------------------------------------ login

    def _active_flow(self, account_id: str) -> LoginFlow | None:
        flow = self._flows.get(account_id)
        if flow is None:
            return None
        if flow.task is not None and flow.task.done():
            return None
        return flow

    async def start_login(self, account_id: str) -> dict[str, str | None]:
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="该账号已有登录流程进行中")
        profile_path = ensure_profile_dir(self._settings, account_id)
        flow = LoginFlow(account_id=account_id)
        self._flows[account_id] = flow
        flow.task = asyncio.create_task(self._login_flow(flow, profile_path))
        return self._login_view(flow)

    async def login_status(self, account_id: str) -> dict[str, str | None]:
        flow = self._flows.get(account_id)
        if flow is None:
            return {"state": "idle", "detail": None}
        return self._login_view(flow)

    async def cancel_login(self, account_id: str) -> dict[str, str | None]:
        flow = self._flows.get(account_id)
        if flow is None:
            return {"state": "idle", "detail": None}
        if flow.task is not None and not flow.task.done():
            flow.task.cancel()
            try:
                await flow.task
            except (asyncio.CancelledError, Exception):
                pass
        self._flows.pop(account_id, None)
        return {"state": "idle", "detail": "已取消"}

    @staticmethod
    def _login_view(flow: LoginFlow) -> dict[str, str | None]:
        return {"state": flow.state, "detail": flow.detail}

    async def _login_flow(self, flow: LoginFlow, profile_path: Path) -> None:
        context = None
        try:
            flow.state = "waiting_scan"
            context, page = await self._open(profile_path, headless=False)
            chat = DouyinChat(page, sleep=self._sleep)
            await chat.open_chat_page()

            logged_in = await chat.wait_logged_in(
                timeout=self._login_timeout,
                poll_interval=self._login_poll_interval,
            )
            if not logged_in:
                flow.state = "expired"
                flow.detail = "等待扫码超时，请重新发起"
                return

            flow.state = "succeeded"
            self._mark_healthy(flow.account_id)
        except asyncio.CancelledError:
            flow.state = "idle"
            raise
        except Exception as exc:
            flow.state = "failed"
            flow.detail = type(exc).__name__
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    def _mark_healthy(self, account_id: str) -> None:
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise RuntimeError("账号不存在")
            account.status = AccountStatus.HEALTHY
            account.last_verified_at = _utcnow()
            session.add(account)
            session.commit()

    def _mark_auth_expired(self, account_id: str) -> None:
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is not None:
                account.status = AccountStatus.AUTH_EXPIRED
                session.add(account)
                session.commit()

    def _touch_verified(self, account_id: str) -> None:
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is not None:
                account.last_verified_at = _utcnow()
                session.add(account)
                session.commit()

    # ------------------------------------------------------------ dry run

    async def run_dry_run(self, account_id: str) -> dict:
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能执行 Dry Run")

        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="账号不存在")
            targets = session.exec(
                select(ContractTarget).where(ContractTarget.account_id == account_id)
            ).all()
        if not targets:
            raise HTTPException(status_code=409, detail="该账号还没有契约对象，请先添加好友")

        profile_path = ensure_profile_dir(self._settings, account_id)
        results: list[DryRunTargetResult] = []
        overall = "ok"
        detail: str | None = None
        context = None
        visible_titles: list[str] = []
        diagnostics: dict | None = None
        try:
            context, page = await self._open(profile_path, headless=True)
            chat = self._make_chat(page)
            await chat.open_chat_page()
            if not await chat.wait_logged_in(
                timeout=self._login_check_timeout,
                poll_interval=self._login_poll_interval,
            ):
                self._mark_auth_expired(account_id)
                overall = "auth_expired"
                detail = "登录已失效，请重新扫码"
            else:
                try:
                    await chat.warm_up_list()
                except Exception:
                    pass
                visible_titles = await chat.visible_titles()
                for target in targets:
                    opened = await chat.open_conversation(target.nickname_snapshot)
                    if not opened.opened:
                        hint = (
                            f"未找到；当前列表可见会话：{', '.join(visible_titles[:12])}"
                            if visible_titles
                            else "未找到；请确认昵称与抖音列表显示完全一致"
                        )
                        results.append(
                            DryRunTargetResult(
                                target.nickname_snapshot, "not_found", None, hint
                            )
                        )
                        if diagnostics is None:
                            diagnostics = (
                                await chat.collect_search_diagnostics(
                                    target.nickname_snapshot
                                )
                                or None
                            )
                    elif not opened.exact:
                        results.append(
                            DryRunTargetResult(
                                target.nickname_snapshot, "ambiguous", opened.title, None
                            )
                        )
                    else:
                        results.append(
                            DryRunTargetResult(
                                target.nickname_snapshot, "found", opened.title, None
                            )
                        )
                self._touch_verified(account_id)
        except Exception as exc:
            overall = "error"
            detail = type(exc).__name__
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

        artifact_path = self._write_dry_run_artifact(account_id, overall, detail, results)
        return {
            "account_id": account_id,
            "overall": overall,
            "detail": detail,
            "targets": [
                {
                    "target": item.target,
                    "status": item.status,
                    "title": item.title,
                    "detail": item.detail,
                }
                for item in results
            ],
            "visible_titles": visible_titles,
            "diagnostics": diagnostics,
            "artifact_path": str(artifact_path),
        }

    def _write_dry_run_artifact(
        self,
        account_id: str,
        overall: str,
        detail: str | None,
        results: list[DryRunTargetResult],
    ) -> Path:
        directory = self._settings.artifacts_dir / account_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"dry-run-{_utcnow():%Y%m%d-%H%M%S}.json"
        payload = {
            "account_id": account_id,
            "overall": overall,
            "detail": detail,
            "finished_at": _utcnow().isoformat(),
            "targets": [
                {
                    "target": item.target,
                    "status": item.status,
                    "title": item.title,
                    "detail": item.detail,
                }
                for item in results
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ------------------------------------------------------------ send

    async def run_send(self, account_id: str) -> dict:
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能发送")

        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="账号不存在")
            if not account.enabled:
                raise HTTPException(status_code=409, detail="账号已暂停")
            targets = session.exec(
                select(ContractTarget).where(
                    ContractTarget.account_id == account_id,
                    ContractTarget.enabled,
                )
            ).all()
        if not targets:
            raise HTTPException(status_code=409, detail="没有启用中的好友，请先到好友页添加")

        profile_path = ensure_profile_dir(self._settings, account_id)
        results: list[dict] = []
        overall = "ok"
        detail: str | None = None
        context = None
        try:
            context, page = await self._open(profile_path, headless=True)
            chat = self._make_chat(page)
            await chat.open_chat_page()
            if not await chat.wait_logged_in(
                timeout=self._login_check_timeout,
                poll_interval=self._login_poll_interval,
            ):
                self._mark_auth_expired(account_id)
                overall = "auth_expired"
                detail = "登录已失效，请重新扫码"
            else:
                business_date = datetime.now(ZoneInfo(account.timezone)).date()
                for target in targets:
                    result = await self._send_one(chat, account, target, business_date)
                    results.append(result)
                    if result["status"] not in ("succeeded", "duplicate_succeeded", "skipped"):
                        overall = "partial"
        except Exception as exc:
            overall = "error"
            detail = type(exc).__name__
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

        artifact_path = self._write_send_artifact(account_id, overall, detail, results)
        return {
            "account_id": account_id,
            "overall": overall,
            "detail": detail,
            "results": results,
            "artifact_path": str(artifact_path),
        }

    async def _send_one(
        self,
        chat: DouyinChat,
        account: Account,
        target: ContractTarget,
        business_date: date,
    ) -> dict:
        with Session(self._engine) as session:
            current = session.get(ContractTarget, target.id)
            if current is None or not current.enabled:
                return {"target": target.nickname_snapshot, "status": "skipped",
                        "detail": "好友已删除或暂停", "failure_class": None, "message": None}
            template = session.get(MessageTemplate, current.template_id) if current.template_id else None
            template_ok = template is not None and template.enabled
            template_id = template.id if template_ok else None
            template_version = template.version if template_ok else None
            template_type = template.type if template_ok else TemplateType.TEXT
            template_content = template.content if template_ok else None
            image_path = template.image_path if template_ok else None
            sticker_name = template.sticker_name if template_ok else None
            sticker_label = template.sticker_label if template_ok else None
            sticker_category = template.sticker_category if template_ok else None
            sticker_fallback_index = template.sticker_fallback_index if template_ok else None
            nickname = current.nickname_snapshot
            streak_days = current.streak_days

        if not template_ok:
            return {"target": nickname, "status": "skipped",
                    "detail": "未绑定可用模板", "failure_class": None, "message": None}
        if template_type == TemplateType.IMAGE and not image_path:
            return {"target": nickname, "status": "skipped",
                    "detail": "图片模板缺少图片路径", "failure_class": None, "message": None}
        if template_type == TemplateType.STICKER and not sticker_name:
            return {"target": nickname, "status": "skipped",
                    "detail": "表情模板缺少表情名称", "failure_class": None, "message": None}

        message = None
        if template_type == TemplateType.TEXT:
            message = render_message(
                template_content,
                target_name=nickname,
                streak_days=streak_days,
                timezone=account.timezone,
            )
        slot = f"{template_id}:v{template_version}"

        job, created = self._get_or_create_job(
            account.id, target.id, template_id, business_date, slot
        )
        if not created:
            if job.status == JobStatus.SUCCEEDED:
                return {"target": nickname, "status": "duplicate_succeeded",
                        "detail": "今日已发送，已跳过", "failure_class": None, "message": message}
            if job.status == JobStatus.UNKNOWN_REQUIRES_REVIEW:
                return {"target": nickname, "status": "duplicate_unknown",
                        "detail": "今日结果未确认，已跳过以防重复", "failure_class": None, "message": message}

        opened = await chat.open_conversation(nickname)
        if not opened.opened:
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.FAILED_TERMINAL, failure=FailureClass.TARGET_UNCERTAIN,
                confirmed=False,
            )
            return {"target": nickname, "status": "not_found",
                    "failure_class": FailureClass.TARGET_UNCERTAIN.value,
                    "detail": "会话列表中未找到该好友", "message": message}
        if not opened.exact:
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.FAILED_TERMINAL, failure=FailureClass.TARGET_UNCERTAIN,
                confirmed=False,
            )
            return {"target": nickname, "status": "ambiguous",
                    "failure_class": FailureClass.TARGET_UNCERTAIN.value,
                    "detail": f"右侧标题为「{opened.title}」，与好友名不一致",
                    "message": message}

        attempt_no = self._record_attempt(job.id)
        try:
            if template_type == TemplateType.IMAGE:
                await chat.send_image(image_path)
            elif template_type == TemplateType.STICKER:
                await chat.send_sticker(
                    Sticker(
                        name=sticker_name,
                        label=sticker_label,
                        category=sticker_category,
                        fallback_index=sticker_fallback_index,
                    )
                )
            else:
                await chat.send_text(message)
        except DomChangedError:
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.FAILED_RETRYABLE, failure=FailureClass.DOM_CHANGED,
                confirmed=False, attempt_no=attempt_no,
            )
            return {"target": nickname, "status": "failed_retryable",
                    "failure_class": FailureClass.DOM_CHANGED.value,
                    "detail": "页面结构变化，未找到输入框", "message": message}
        except Exception as exc:
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.FAILED_RETRYABLE, failure=FailureClass.OTHER,
                confirmed=False, attempt_no=attempt_no,
            )
            return {"target": nickname, "status": "failed_retryable",
                    "failure_class": FailureClass.OTHER.value,
                    "detail": type(exc).__name__, "message": message}

        self._update_job_status(job.id, JobStatus.CONFIRMING)
        outcome = await self._confirm_send(chat, template_type)

        outcome_map = {
            "succeeded": (JobStatus.SUCCEEDED, None, True),
            "failed_retryable": (JobStatus.FAILED_RETRYABLE, FailureClass.OTHER, False),
            "unknown_requires_review": (
                JobStatus.UNKNOWN_REQUIRES_REVIEW,
                FailureClass.UNKNOWN_OUTCOME,
                False,
            ),
        }
        job_status, failure, confirmed = outcome_map[outcome]
        bubble_confirmed = bool(getattr(chat, "last_bubble_seen", False))
        upgrade_detail: str | None = None
        if outcome == "unknown_requires_review" and bubble_confirmed:
            # 权威证据：消息已进入消息列表气泡，且整个观察窗内无失败标记。
            # 仅状态指示未消失，按成功核销，防止重复发送。
            job_status = JobStatus.SUCCEEDED
            failure = None
            confirmed = True
            outcome = "succeeded"
            upgrade_detail = "消息已出现在聊天记录中（经气泡内容确认）"
        else:
            upgrade_detail = (
                "已触发发送但结果未确认，请到任务记录人工核对"
                if outcome == "unknown_requires_review"
                else None
            )
        self._finalize_send(
            job.id, target.id, account.id,
            status=job_status, failure=failure, confirmed=confirmed, attempt_no=attempt_no,
        )
        return {"target": nickname, "status": outcome,
                "failure_class": failure.value if failure else None,
                "detail": upgrade_detail, "message": message}

    async def _confirm_send(self, chat: DouyinChat, template_type: TemplateType) -> str:
        """发送后的终态确认。

        文本：SendConfirmer（编辑框清空 + 无失败标记）。
        图片/表情：编辑框清空不适用（图片/表情后编辑框可能本就空），改为等待
              「从我的 + 非头像」新增气泡出现 + 无失败标记稳定。若气泡未出现/
              有失败标记 → unknown 或 failed。表情与图片都锚定非头像增图。
        """
        if template_type in (TemplateType.IMAGE, TemplateType.STICKER):
            if await chat.failure_marker_visible():
                return "failed_retryable"
            # 图片/表情：轮询等待「从我的 + 非头像图片」气泡出现（上传/发送有延迟，
            # 用 image_bubble_confirmed 主动查询，比 wait_for_function 更可靠）。
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._send_timeout
            while loop.time() < deadline:
                if await chat.failure_marker_visible():
                    return "failed_retryable"
                if await chat.image_bubble_confirmed():
                    return "succeeded"
                await self._sleep(self._send_poll_interval)
            # 无失败标记但未确认到图片气泡 → unknown（绝不自动重发）
            return "unknown_requires_review"

        return await SendConfirmer(
            chat,
            sleep=self._sleep,
            timeout=self._send_timeout,
            poll_interval=self._send_poll_interval,
            stabilize_polls=self._send_stabilize_polls,
        ).confirm()

    def _get_or_create_job(
        self,
        account_id: str,
        target_id: str,
        template_id: str,
        business_date: date,
        slot: str,
    ) -> tuple[SendJob, bool]:
        query = (
            SendJob.account_id == account_id,
            SendJob.target_id == target_id,
            SendJob.business_date == business_date,
            SendJob.message_slot_id == slot,
        )
        with Session(self._engine) as session:
            existing = session.exec(select(SendJob).where(*query)).first()
            if existing is not None:
                return existing, False
            job = SendJob(
                account_id=account_id,
                target_id=target_id,
                template_id=template_id,
                business_date=business_date,
                message_slot_id=slot,
                status=JobStatus.SCHEDULED,
                scheduled_at=_utcnow(),
            )
            session.add(job)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.exec(select(SendJob).where(*query)).first()
                return existing, False
            session.refresh(job)
            return job, True

    def _record_attempt(self, job_id: str) -> int:
        with Session(self._engine) as session:
            job = session.get(SendJob, job_id)
            if job is None:
                raise RuntimeError("任务不存在")
            job.attempt_count += 1
            attempt_no = job.attempt_count
            job.status = JobStatus.SENDING
            job.updated_at = _utcnow()
            attempt = SendAttempt(
                job_id=job_id,
                attempt_no=attempt_no,
                stage="sending",
                status=AttemptStatus.RUNNING,
                started_at=_utcnow(),
            )
            session.add(job)
            session.add(attempt)
            session.commit()
            return attempt_no

    def _update_job_status(self, job_id: str, status: JobStatus) -> None:
        with Session(self._engine) as session:
            job = session.get(SendJob, job_id)
            if job is not None:
                job.status = status
                job.updated_at = _utcnow()
                session.add(job)
                session.commit()

    def _finalize_send(
        self,
        job_id: str,
        target_id: str,
        account_id: str,
        *,
        status: JobStatus,
        failure: FailureClass | None,
        confirmed: bool,
        attempt_no: int | None = None,
    ) -> None:
        with Session(self._engine) as session:
            now = _utcnow()
            job = session.get(SendJob, job_id)
            if job is not None:
                job.status = status
                job.failure_class = failure
                job.updated_at = now
                if confirmed:
                    job.confirmed_at = now
                session.add(job)
            if attempt_no is not None:
                attempt = session.exec(
                    select(SendAttempt).where(
                        SendAttempt.job_id == job_id,
                        SendAttempt.attempt_no == attempt_no,
                    )
                ).first()
                if attempt is not None:
                    attempt.status = (
                        AttemptStatus.SUCCEEDED if confirmed else AttemptStatus.FAILED
                    )
                    attempt.failure_class = failure
                    attempt.finished_at = now
                    session.add(attempt)
            if confirmed:
                target = session.get(ContractTarget, target_id)
                if target is not None:
                    target.last_sent_at = now
                    target.last_confirmed_at = now
                    target.streak_days += 1
                    target.updated_at = now
                    session.add(target)
                account = session.get(Account, account_id)
                if account is not None:
                    account.last_success_at = now
                    session.add(account)
            session.commit()

    def _write_send_artifact(
        self,
        account_id: str,
        overall: str,
        detail: str | None,
        results: list[dict],
    ) -> Path:
        directory = self._settings.artifacts_dir / account_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"send-{_utcnow():%Y%m%d-%H%M%S}.json"
        payload = {
            "account_id": account_id,
            "overall": overall,
            "detail": detail,
            "finished_at": _utcnow().isoformat(),
            "results": results,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ------------------------------------------------------------ shutdown

    async def aclose(self) -> None:
        for flow in list(self._flows.values()):
            if flow.task is not None and not flow.task.done():
                flow.task.cancel()
        for flow in list(self._flows.values()):
            if flow.task is not None:
                try:
                    await flow.task
                except (asyncio.CancelledError, Exception):
                    pass
        self._flows.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
