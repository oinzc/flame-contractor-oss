import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import HTTPException
from sqlmodel import Session, select

from ..core.config import Settings
from ..models import Account, AccountStatus, ContractTarget
from .douyin_chat import DouyinChat
from .profile_store import ensure_profile_dir

LOGIN_POLL_INTERVAL = 2.0
LOGIN_TIMEOUT_SECONDS = 180.0

BrowserFactory = Callable[[Path, bool], Awaitable[tuple[object, object]]]


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
        sleep=asyncio.sleep,
    ):
        self._engine = engine
        self._settings = settings
        self._flows: dict[str, LoginFlow] = {}
        self._playwright = None
        self._browser_factory = browser_factory
        self._login_poll_interval = login_poll_interval
        self._login_timeout = login_timeout
        self._sleep = sleep

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

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._login_timeout
            logged_in = await chat.is_logged_in()
            while not logged_in:
                if loop.time() >= deadline:
                    flow.state = "expired"
                    flow.detail = "等待扫码超时，请重新发起"
                    return
                await self._sleep(self._login_poll_interval)
                logged_in = await chat.is_logged_in()

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
        try:
            context, page = await self._open(profile_path, headless=True)
            chat = DouyinChat(page, sleep=self._sleep)
            await chat.open_chat_page()
            if not await chat.is_logged_in():
                self._mark_auth_expired(account_id)
                overall = "auth_expired"
                detail = "登录已失效，请重新扫码"
            else:
                for target in targets:
                    opened = await chat.open_conversation(target.nickname_snapshot)
                    if not opened.opened:
                        results.append(DryRunTargetResult(target.nickname_snapshot, "not_found"))
                    elif not opened.exact:
                        results.append(
                            DryRunTargetResult(target.nickname_snapshot, "ambiguous", opened.title)
                        )
                    else:
                        results.append(
                            DryRunTargetResult(target.nickname_snapshot, "found", opened.title)
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
                {"target": item.target, "status": item.status, "title": item.title}
                for item in results
            ],
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
                {"target": item.target, "status": item.status, "title": item.title}
                for item in results
            ],
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
