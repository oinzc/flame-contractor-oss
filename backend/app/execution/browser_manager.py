import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Literal
from uuid import uuid4
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
    ScanTask,
    ScanTaskStatus,
    IdentityHydrationTask,
    TemplateType,
)
from ..schemas import BatchTargetItem, TargetBatchCreate
from ..notifications import Notifier, NullNotifier
from ..scan_reconciliation import (
    build_reconciliation,
    empty_reconciliation,
    match_scan_entry_to_target,
    parse_reconciliation,
    target_snapshot,
)
from ..services import create_targets_batch
from .douyin_chat import (
    SCAN_MAX_ROUNDS,
    DomChangedError,
    DouyinChat,
    SendOutcomeUnknownError,
    Sticker,
    read_qr_data_url,
    read_self_nickname,
)
from .message_renderer import render_message
from .pool import pick_pool_item
from .profile_store import ensure_profile_dir
from .selectors import bundle_digest
from .send_confirmer import SendConfirmer

logger = logging.getLogger(__name__)

# 登录判定预算（秒）：页面打开后，等会话列表出现的时长。判定成功会立即返回，因此
# 放宽只影响「真的没登录/页面没挂上」时的确认耗时，不拖慢正常路径。
# 原为 8 秒——而冷启动实测「打开会话页 4.3s + 判定 1.3s」已接近该上限，慢网络下
# 列表渲染稍晚就会被判成「登录失效」（截图扫码路径用的是 120 秒，同一判据差 15 倍）。
LOGIN_CHECK_TIMEOUT = 20.0
LOGIN_POLL_INTERVAL = 2.0
# 登录判定容忍次数：连续失败达到该次数才把账号标记为登录失效（AUTH_EXPIRED）。
# 单次判定抖动（页面加载慢、临时验证页、网络波动）曾直接把账号判为失效，而调度器
# 会跳过所有非 HEALTHY 账号的扫描与发送，于是账号彻底停摆、只能人工重新扫码救回
# （实测案例：一次自动扫描在 8 秒判定窗口内误判失效，而同一时刻会话其实有效）。
AUTH_CHECK_TOLERANCE = 2
# 登录判定结论：ok=通过 / auth_expired=已确认失效 / auth_unconfirmed=单次失败未确认。
SESSION_OK = "ok"
SESSION_AUTH_EXPIRED = "auth_expired"
SESSION_AUTH_UNCONFIRMED = "auth_unconfirmed"
# 页面根本没加载出来（Chromium 错误页），此时「找不到会话列表」说明网络/加载有问题，
# 不能当成「登录失效」——否则一次网络抖动会以「请重新扫码」的面目出现并误导用户。
SESSION_NETWORK_PROBLEM = "network_problem"
# 判定「页面未加载成功」的 URL 前缀：Chromium 的离线/连接错误页。
_LOAD_FAILURE_URL_PREFIXES = ("chrome-error://", "about:blank", "data:text/html")
LOGIN_TIMEOUT_SECONDS = 180.0
# 截图扫码：二维码在页面上真正渲染出来需要的时间（慢网络下可达数十秒）
QR_PREPARE_TIMEOUT = 120.0
QR_PREPARE_POLL_INTERVAL = 2.0
SEND_TIMEOUT_SECONDS = 15.0
SEND_POLL_INTERVAL = 0.5
SEND_STABILIZE_POLLS = 3

BrowserFactory = Callable[[Path, bool], Awaitable[tuple[object, object]]]

# Windows 有头窗口会被 Chromium 的原生遮挡计算误判为被遮挡而停止绘制（整页黑屏，
# headless 不受影响）；关闭该特性规避。headless 携带无害，故统一传入。
CHROMIUM_LAUNCH_ARGS = ["--disable-features=CalculateNativeWinOcclusion"]

# 扫码后抖音弹「身份验证」风控时的引导文案：验证方式与验证码都在页面里
# 操作，扫码人手机上点完登录就没有后续了——所以必须由维护者在控制台的
# 登录面板远程输验证码，而不是等谁去 App 里点确认。
LOGIN_VERIFICATION_GUIDANCE = (
    "抖音要求身份验证（新环境登录的风控）。请在下方选择验证方式并输入验证码"
    "完成验证（验证码会发到该账号绑定的手机/邮箱，需要向账号本人索取）；"
    "推荐先试「授权历史记录验证」。验证通过后登录会自动继续；"
    "等待中，超时后请重新发起。"
)
# 默认自动选择的验证方式：短信最常见、账号本人最容易拿到码。自动点选失败
# 时回退为手动按钮（其他方式仍可改选）。文案以真实弹窗为准。
LOGIN_VERIFICATION_AUTO_OPTION = "手机验证码验证"
LOGIN_VERIFICATION_SMS_PENDING = (
    "已自动选择「手机验证码验证」，验证码已发送到该账号绑定的手机。"
    "请向账号本人索取验证码并在下方输入提交；收不到可改选其他方式。"
)


def headed_login_available() -> bool:
    """当前主机能否弹出有头浏览器窗口。

    容器与纯服务器部署没有 X 显示，有头浏览器必然启动失败；该判断独立成函数，
    便于测试直接替换，而不必改动进程环境。
    """
    return not (sys.platform.startswith("linux") and not os.environ.get("DISPLAY"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LoginFlow:
    account_id: str
    state: str = "waiting_scan"
    detail: str | None = None
    task: asyncio.Task | None = None
    # M8-4 截图扫码登录：headless 登录页截图的落盘路径
    qr_image: str | None = None
    # 登录成功后读到的当前抖音身份昵称（localStorage user_info，仅展示）
    self_nickname: str | None = None
    # 「身份验证」远程交互：验证等待期间挂着的 DouyinChat，结束后置空
    chat: DouyinChat | None = None
    # 已自动点选的验证方式（None = 尚未自动选择，仍展示手动按钮）
    auto_option: str | None = None


@dataclass
class DryRunTargetResult:
    target: str
    status: str  # found | not_found | ambiguous
    title: str | None = None
    detail: str | None = None


@dataclass
class SendProgress:
    """单账号发送流程的内存快照，供前端展示可核验的真实进度。"""

    account_id: str
    total: int
    completed: int = 0
    status: str = "running"  # running | completed | failed
    stage: str = "准备发送"
    current_target: str | None = None
    last_result: str | None = None
    overall: str | None = None
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class ScanProgress:
    """单次会话扫描快照；数据库负责跨进程生命周期保存。"""

    task_id: str
    account_id: str
    source: Literal["manual", "automatic"] = "manual"
    total: int | None = None
    completed: int = 0
    status: str = "running"  # running | completed | failed | interrupted | cancelled
    stage: str = "准备扫描"
    current_item: str | None = None
    round: int = 0
    max_rounds: int = SCAN_MAX_ROUNDS
    overall: str | None = None
    detail: str | None = None
    entries: list[dict] | None = None
    reconciliation: dict | None = None
    # Captured before opening the browser so concurrent imports/deletions do
    # not change the meaning of this scan's comparison.
    targets_before_scan: list[dict] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class IdentityHydrationProgress:
    """普通好友身份补采快照；结果与进度一起持久化到 SQLite。"""

    task_id: str
    account_id: str
    target_ids: list[str]
    total: int
    completed: int = 0
    status: str = "running"  # running | completed | failed | interrupted | cancelled
    stage: str = "准备补采身份"
    current_item: str | None = None
    overall: str | None = None
    detail: str | None = None
    results: list[dict] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


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
        auth_check_tolerance: int = AUTH_CHECK_TOLERANCE,
        send_timeout: float = SEND_TIMEOUT_SECONDS,
        send_poll_interval: float = SEND_POLL_INTERVAL,
        send_stabilize_polls: int = SEND_STABILIZE_POLLS,
        send_cooldown: float = 3.0,
        send_cooldown_jitter: float = 0.0,
        network_checker: Callable[[], Awaitable[bool]] | None = None,
        qr_login_timeout: float = 300.0,
        qr_screenshot_interval: float = 6.0,
        confirm_timeout: float = 8.0,
        search_timeout: float = 12.0,
        notifier: Notifier | None = None,
        sleep=asyncio.sleep,
        rng: random.Random | None = None,
    ):
        self._engine = engine
        self._settings = settings
        # 扫码登录是账号恢复自动续火的唯一入口：失败/超时必须能主动回音，不能只在日志留痕。
        self._notifier: Notifier = notifier or NullNotifier()
        self._flows: dict[str, LoginFlow] = {}
        self._send_progress: dict[str, SendProgress] = {}
        self._scan_progress: dict[str, ScanProgress] = {}
        self._scan_tasks: dict[str, asyncio.Task] = {}
        self._identity_hydration_progress: dict[str, IdentityHydrationProgress] = {}
        self._identity_hydration_tasks: dict[str, asyncio.Task] = {}
        self._playwright = None
        self._browser_factory = browser_factory
        self._login_poll_interval = login_poll_interval
        self._login_timeout = login_timeout
        self._login_check_timeout = login_check_timeout
        # 连续登录判定失败计数：只在内存，进程重启即清零——宁可「少一次判定」也不要
        # 因为重启把计数带过去而误落终局。
        self._auth_check_tolerance = max(1, int(auth_check_tolerance))
        self._auth_check_failures: dict[str, int] = {}
        # 最近一次登录判定失败时的现场证据（截图路径与页面 URL），供调用方写进诊断工件。
        self._auth_evidence: dict[str, dict] = {}
        self._send_timeout = send_timeout
        self._send_poll_interval = send_poll_interval
        self._send_stabilize_polls = send_stabilize_polls
        self._confirm_timeout = confirm_timeout
        self._search_timeout = search_timeout
        self._send_cooldown = send_cooldown
        # 冷却随机抖动上限：实际等待 = send_cooldown + uniform(0, jitter)，
        # 消除固定间隔的机器节律；0 = 关闭抖动（保持固定间隔，测试默认）。
        self._send_cooldown_jitter = max(0.0, float(send_cooldown_jitter))
        self._rng = rng or random.Random()
        self._network_checker = network_checker
        self._qr_login_timeout = qr_login_timeout
        self._qr_screenshot_interval = qr_screenshot_interval
        self._sleep = sleep

    def _cooldown_delay(self) -> float:
        """好友间冷却时长：下限 + 随机抖动。

        固定间隔（每天精确 3 秒）是明显的自动化节律特征；抖动后每次等待
        落在 [send_cooldown, send_cooldown + jitter] 区间，下限仍保证冷却效果。
        """
        if self._send_cooldown_jitter <= 0:
            return self._send_cooldown
        return self._send_cooldown + self._rng.uniform(0, self._send_cooldown_jitter)

    def _start_send_progress(self, account_id: str, total: int) -> None:
        self._send_progress[account_id] = SendProgress(
            account_id=account_id,
            total=total,
            started_at=_utcnow(),
        )

    def _update_send_progress(self, account_id: str, **changes: object) -> None:
        progress = self._send_progress.get(account_id)
        if progress is None:
            return
        for key, value in changes.items():
            if hasattr(progress, key):
                setattr(progress, key, value)

    def _finish_send_progress(
        self,
        account_id: str,
        *,
        status: str,
        completed: int,
        stage: str,
        overall: str,
        detail: str | None,
    ) -> None:
        self._update_send_progress(
            account_id,
            status=status,
            completed=completed,
            stage=stage,
            current_target=None,
            overall=overall,
            detail=detail,
            finished_at=_utcnow(),
        )

    def send_progress(self, account_id: str) -> dict:
        """返回可序列化的发送进度；不暴露浏览器、消息或凭据信息。"""
        progress = self._send_progress.get(account_id)
        if progress is None:
            return {
                "account_id": account_id,
                "status": "idle",
                "total": 0,
                "completed": 0,
                "current_target": None,
                "stage": "未开始",
                "last_result": None,
                "overall": None,
                "detail": None,
                "started_at": None,
                "finished_at": None,
            }
        return {
            "account_id": progress.account_id,
            "status": progress.status,
            "total": progress.total,
            "completed": progress.completed,
            "current_target": progress.current_target,
            "stage": progress.stage,
            "last_result": progress.last_result,
            "overall": progress.overall,
            "detail": progress.detail,
            "started_at": progress.started_at,
            "finished_at": progress.finished_at,
        }

    def _active_scan(self, account_id: str) -> ScanProgress | None:
        for progress in reversed(list(self._scan_progress.values())):
            if progress.account_id == account_id and progress.status == "running":
                return progress
        return None

    @staticmethod
    def _scan_entries_from_json(value: str | None) -> list[dict]:
        if not value:
            return []
        try:
            entries = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return entries if isinstance(entries, list) else []

    def _scan_progress_view_from_record(self, task: ScanTask) -> dict:
        status = task.status.value if isinstance(task.status, ScanTaskStatus) else str(task.status)
        return {
            "task_id": task.id,
            "account_id": task.account_id,
            "source": task.source,
            "status": status,
            "total": task.total,
            "completed": task.completed,
            "stage": task.stage,
            "current_item": task.current_item,
            "round": task.round,
            "max_rounds": task.max_rounds,
            "overall": task.overall,
            "detail": task.detail,
            "entries": self._scan_entries_from_json(task.entries_json),
            "reconciliation": parse_reconciliation(task.reconciliation_json),
            "started_at": task.started_at,
            "finished_at": task.finished_at,
        }

    def _persist_scan_progress(self, progress: ScanProgress) -> None:
        """把内存快照写入 SQLite；每次提交都是一个短事务，避免占住浏览器流程。"""
        with Session(self._engine) as session:
            task = session.get(ScanTask, progress.task_id)
            if task is None:
                return
            task.status = ScanTaskStatus(progress.status)
            task.total = progress.total
            task.completed = progress.completed
            task.stage = progress.stage
            task.current_item = progress.current_item
            task.round = progress.round
            task.max_rounds = progress.max_rounds
            task.overall = progress.overall
            task.detail = progress.detail
            task.entries_json = json.dumps(
                progress.entries or [], ensure_ascii=False, separators=(",", ":")
            )
            task.reconciliation_json = json.dumps(
                progress.reconciliation or empty_reconciliation(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            task.started_at = progress.started_at or task.started_at
            task.finished_at = progress.finished_at
            task.updated_at = _utcnow()
            session.add(task)
            session.commit()

    def _scan_progress_view(self, progress: ScanProgress) -> dict:
        return {
            "task_id": progress.task_id,
            "account_id": progress.account_id,
            "source": progress.source,
            "status": progress.status,
            "total": progress.total,
            "completed": progress.completed,
            "stage": progress.stage,
            "current_item": progress.current_item,
            "round": progress.round,
            "max_rounds": progress.max_rounds,
            "overall": progress.overall,
            "detail": progress.detail,
            "entries": progress.entries or [],
            "reconciliation": progress.reconciliation or empty_reconciliation(),
            "started_at": progress.started_at,
            "finished_at": progress.finished_at,
        }

    def _update_scan_progress(self, task_id: str, **changes: object) -> None:
        progress = self._scan_progress.get(task_id)
        if progress is None:
            return
        for key, value in changes.items():
            if hasattr(progress, key):
                setattr(progress, key, value)
        self._persist_scan_progress(progress)

    async def start_scan_chats(
        self,
        account_id: str,
        *,
        source: Literal["manual", "automatic"] = "manual",
    ) -> dict:
        """创建后台扫描任务；浏览器扫描不会绑定前端页面生命周期。"""
        if source not in ("manual", "automatic"):
            raise ValueError("扫描任务来源无效")
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能扫描会话")
        if self._active_scan(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号已有会话扫描进行中")
        send_progress = self._send_progress.get(account_id)
        if send_progress is not None and send_progress.status == "running":
            raise HTTPException(status_code=409, detail="账号正在发送，暂不能扫描会话")

        with Session(self._engine) as session:
            if session.get(Account, account_id) is None:
                raise HTTPException(status_code=404, detail="账号不存在")
            targets_before_scan = [
                target_snapshot(target)
                for target in session.exec(
                    select(ContractTarget).where(
                        ContractTarget.account_id == account_id,
                        ContractTarget.deleted_at.is_(None),
                    )
                ).all()
            ]

        progress = ScanProgress(
            task_id=str(uuid4()),
            account_id=account_id,
            source=source,
            reconciliation=empty_reconciliation(),
            targets_before_scan=targets_before_scan,
            started_at=_utcnow(),
        )
        with Session(self._engine) as session:
            session.add(
                ScanTask(
                    id=progress.task_id,
                    account_id=account_id,
                    source=source,
                    status=ScanTaskStatus.RUNNING,
                    total=progress.total,
                    completed=progress.completed,
                    stage=progress.stage,
                    current_item=progress.current_item,
                    round=progress.round,
                    max_rounds=progress.max_rounds,
                    entries_json="[]",
                    reconciliation_json=json.dumps(
                        empty_reconciliation(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    started_at=progress.started_at or _utcnow(),
                    updated_at=progress.started_at or _utcnow(),
                )
            )
            session.commit()
        self._scan_progress[progress.task_id] = progress
        task = asyncio.create_task(self._run_scan_task(progress))
        self._scan_tasks[progress.task_id] = task
        task.add_done_callback(lambda _: self._scan_tasks.pop(progress.task_id, None))
        return self._scan_progress_view(progress)

    async def _run_scan_task(self, progress: ScanProgress) -> None:
        try:
            result = await self._scan_chats_impl(progress.account_id, progress)
            overall = result["overall"]
            if overall == "ok":
                entries = result["entries"]
                reconciliation = build_reconciliation(
                    progress.targets_before_scan or [],
                    entries,
                )
                self._update_scan_progress(
                    progress.task_id,
                    status="completed",
                    total=len(entries),
                    completed=len(entries),
                    stage="扫描完成",
                    current_item=None,
                    overall=overall,
                    detail=None,
                    entries=entries,
                    reconciliation=reconciliation,
                    finished_at=_utcnow(),
                )
            else:
                entries = result.get("entries") or progress.entries or []
                self._update_scan_progress(
                    progress.task_id,
                    status="failed",
                    stage="登录失效" if overall == "auth_expired" else "扫描失败",
                    current_item=None,
                    overall=overall,
                    detail=result["detail"],
                    entries=entries,
                    finished_at=_utcnow(),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._update_scan_progress(
                progress.task_id,
                status="failed",
                stage="流程中断",
                current_item=None,
                overall="error",
                detail=type(exc).__name__,
                entries=progress.entries or [],
                finished_at=_utcnow(),
            )

    def scan_chats_progress(self, account_id: str, task_id: str) -> dict:
        progress = self._scan_progress.get(task_id)
        if progress is not None:
            if progress.account_id != account_id:
                raise HTTPException(status_code=404, detail="扫描任务不存在")
            return self._scan_progress_view(progress)
        with Session(self._engine) as session:
            task = session.get(ScanTask, task_id)
            if task is None or task.account_id != account_id:
                raise HTTPException(status_code=404, detail="扫描任务不存在")
            return self._scan_progress_view_from_record(task)

    async def cancel_scan_chats(self, account_id: str, task_id: str) -> dict:
        """取消后台扫描并保留已经发现的会话；重复取消是幂等的。"""
        progress = self._scan_progress.get(task_id)
        if progress is None:
            with Session(self._engine) as session:
                task = session.get(ScanTask, task_id)
                if task is None or task.account_id != account_id:
                    raise HTTPException(status_code=404, detail="扫描任务不存在")
                if task.status == ScanTaskStatus.RUNNING:
                    now = _utcnow()
                    task.status = ScanTaskStatus.CANCELLED
                    task.stage = "已取消扫描"
                    task.current_item = None
                    task.detail = "用户取消扫描；已保留已发现结果"
                    task.finished_at = now
                    task.updated_at = now
                    session.add(task)
                    session.commit()
                return self._scan_progress_view_from_record(task)

        if progress.account_id != account_id:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        if progress.status != "running":
            return self._scan_progress_view(progress)

        task = self._scan_tasks.get(task_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 任务可能恰好在取消请求抵达前完成；此时尊重真实终态，不覆盖完成/失败结果。
        if progress.status == "running":
            self._update_scan_progress(
                task_id,
                status="cancelled",
                stage="已取消扫描",
                current_item=None,
                overall=None,
                detail="用户取消扫描；已保留已发现结果",
                entries=progress.entries or [],
                finished_at=_utcnow(),
            )
        return self._scan_progress_view(progress)

    def latest_scan_chats(self, account_id: str) -> dict | None:
        """读取账号最近一次扫描；用于前端刷新或新后端进程恢复任务坞。"""
        with Session(self._engine) as session:
            task = session.exec(
                select(ScanTask)
                .where(ScanTask.account_id == account_id)
                .order_by(ScanTask.updated_at.desc())
                .limit(1)
            ).first()
            if task is None:
                return None
            progress = self._scan_progress.get(task.id)
            if progress is not None:
                return self._scan_progress_view(progress)
            return self._scan_progress_view_from_record(task)

    @staticmethod
    def _identity_hydration_results_from_json(value: str | None) -> list[dict]:
        if not value:
            return []
        try:
            results = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return results if isinstance(results, list) else []

    def _identity_hydration_progress_view_from_record(
        self, task: IdentityHydrationTask
    ) -> dict:
        status = task.status.value if isinstance(task.status, ScanTaskStatus) else str(task.status)
        return {
            "task_id": task.id,
            "account_id": task.account_id,
            "status": status,
            "total": task.total,
            "completed": task.completed,
            "stage": task.stage,
            "current_item": task.current_item,
            "overall": task.overall,
            "detail": task.detail,
            "results": self._identity_hydration_results_from_json(task.results_json),
            "started_at": task.started_at,
            "finished_at": task.finished_at,
        }

    def _persist_identity_hydration_progress(
        self, progress: IdentityHydrationProgress
    ) -> None:
        """把身份补采快照写入 SQLite；每次只占用一个短事务。"""
        with Session(self._engine) as session:
            task = session.get(IdentityHydrationTask, progress.task_id)
            if task is None:
                return
            task.status = ScanTaskStatus(progress.status)
            task.total = progress.total
            task.completed = progress.completed
            task.stage = progress.stage
            task.current_item = progress.current_item
            task.overall = progress.overall
            task.detail = progress.detail
            task.results_json = json.dumps(
                progress.results or [], ensure_ascii=False, separators=(",", ":")
            )
            task.started_at = progress.started_at or task.started_at
            task.finished_at = progress.finished_at
            task.updated_at = _utcnow()
            session.add(task)
            session.commit()

    def _identity_hydration_progress_view(
        self, progress: IdentityHydrationProgress
    ) -> dict:
        return {
            "task_id": progress.task_id,
            "account_id": progress.account_id,
            "status": progress.status,
            "total": progress.total,
            "completed": progress.completed,
            "stage": progress.stage,
            "current_item": progress.current_item,
            "overall": progress.overall,
            "detail": progress.detail,
            "results": progress.results or [],
            "started_at": progress.started_at,
            "finished_at": progress.finished_at,
        }

    def _update_identity_hydration_progress(
        self, task_id: str, **changes: object
    ) -> None:
        progress = self._identity_hydration_progress.get(task_id)
        if progress is None:
            return
        for key, value in changes.items():
            if hasattr(progress, key):
                setattr(progress, key, value)
        self._persist_identity_hydration_progress(progress)

    def _active_identity_hydration(self, account_id: str) -> IdentityHydrationProgress | None:
        for progress in reversed(list(self._identity_hydration_progress.values())):
            if progress.account_id == account_id and progress.status == "running":
                return progress
        return None

    async def start_identity_hydration(
        self, account_id: str, target_ids: list[str]
    ) -> dict:
        """为已存在的普通好友逐个打开会话，补采并安全回填稳定身份。"""
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能补采身份")
        if self._active_scan(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在扫描会话，暂不能补采身份")
        if self._active_identity_hydration(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号已有身份补采进行中")
        send_progress = self._send_progress.get(account_id)
        if send_progress is not None and send_progress.status == "running":
            raise HTTPException(status_code=409, detail="账号正在发送，暂不能补采身份")

        ordered_ids = list(dict.fromkeys(target_id.strip() for target_id in target_ids if target_id.strip()))
        with Session(self._engine) as session:
            if session.get(Account, account_id) is None:
                raise HTTPException(status_code=404, detail="账号不存在")
            targets_by_id = {
                target.id: target
                for target in session.exec(
                    select(ContractTarget).where(ContractTarget.id.in_(ordered_ids))
                ).all()
            }
            if len(targets_by_id) != len(ordered_ids) or any(
                targets_by_id[target_id].account_id != account_id for target_id in ordered_ids if target_id in targets_by_id
            ):
                raise HTTPException(status_code=404, detail="所选好友不存在或不属于当前账号")
            eligible = [
                targets_by_id[target_id]
                for target_id in ordered_ids
                if targets_by_id[target_id].platform_user_id is None
                and targets_by_id[target_id].platform_conversation_id is None
                and targets_by_id[target_id].conversation_type != "group"
            ]
            eligible = [target for target in eligible if target.deleted_at is None]

        if not eligible:
            raise HTTPException(status_code=409, detail="所选好友中没有需要补采的普通好友")

        now = _utcnow()
        progress = IdentityHydrationProgress(
            task_id=str(uuid4()),
            account_id=account_id,
            target_ids=[target.id for target in eligible],
            total=len(eligible),
            results=[
                {
                    "target_id": target.id,
                    "nickname": target.nickname_snapshot,
                    "status": "not_observed",
                    "opened": False,
                    "platform_user_id": None,
                    "platform_handle": None,
                    "identity_source": None,
                    "identity_candidate_count": 0,
                    "detail": "等待补采",
                }
                for target in eligible
            ],
            started_at=now,
        )
        with Session(self._engine) as session:
            session.add(
                IdentityHydrationTask(
                    id=progress.task_id,
                    account_id=account_id,
                    status=ScanTaskStatus.RUNNING,
                    total=progress.total,
                    completed=progress.completed,
                    stage=progress.stage,
                    current_item=progress.current_item,
                    results_json=json.dumps(
                        progress.results, ensure_ascii=False, separators=(",", ":")
                    ),
                    started_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        self._identity_hydration_progress[progress.task_id] = progress
        task = asyncio.create_task(self._run_identity_hydration_task(progress))
        self._identity_hydration_tasks[progress.task_id] = task
        task.add_done_callback(lambda _: self._identity_hydration_tasks.pop(progress.task_id, None))
        return self._identity_hydration_progress_view(progress)

    async def _run_identity_hydration_task(
        self, progress: IdentityHydrationProgress
    ) -> None:
        try:
            result = await self._identity_hydration_impl(progress.account_id, progress)
            overall = result["overall"]
            if overall in {"ok", "partial"}:
                self._update_identity_hydration_progress(
                    progress.task_id,
                    status="completed",
                    completed=progress.total,
                    current_item=None,
                    stage="身份补采完成",
                    overall=overall,
                    detail=result.get("detail"),
                    results=result.get("results") or progress.results or [],
                    finished_at=_utcnow(),
                )
            else:
                self._update_identity_hydration_progress(
                    progress.task_id,
                    status="failed",
                    current_item=None,
                    stage="登录失效" if overall == "auth_expired" else "身份补采失败",
                    overall=overall,
                    detail=result.get("detail"),
                    results=result.get("results") or progress.results or [],
                    finished_at=_utcnow(),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._update_identity_hydration_progress(
                progress.task_id,
                status="failed",
                current_item=None,
                stage="流程中断",
                overall="error",
                detail=type(exc).__name__,
                results=progress.results or [],
                finished_at=_utcnow(),
            )

    def _bind_hydrated_identity(
        self,
        account_id: str,
        target_id: str,
        platform_user_id: str,
        platform_handle: str | None,
    ) -> tuple[bool, str | None]:
        """只给原目标回填身份；已有其他目标占用同一 ID 时拒绝覆盖。"""
        with Session(self._engine) as session:
            target = session.get(ContractTarget, target_id)
            if target is None or target.account_id != account_id:
                return False, "好友已不存在或不属于当前账号"
            if target.platform_user_id is not None:
                if target.platform_user_id == platform_user_id:
                    return True, None
                return False, "好友已有不同稳定身份，拒绝覆盖"
            conflict = session.exec(
                select(ContractTarget).where(
                    ContractTarget.account_id == account_id,
                    ContractTarget.platform_user_id == platform_user_id,
                    ContractTarget.id != target_id,
                )
            ).first()
            if conflict is not None:
                return False, f"该稳定身份已绑定好友「{conflict.nickname_snapshot}」，拒绝覆盖"
            target.platform_user_id = platform_user_id
            target.platform_handle = platform_handle
            target.conversation_type = "user"
            target.updated_at = _utcnow()
            session.add(target)
            session.commit()
        return True, None

    async def _identity_hydration_impl(
        self,
        account_id: str,
        progress: IdentityHydrationProgress,
    ) -> dict:
        """执行身份补采；浏览器只打开会话，不触发发送动作。"""
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                return {"overall": "error", "detail": "账号不存在", "results": []}
            targets = session.exec(
                select(ContractTarget).where(ContractTarget.id.in_(progress.target_ids))
            ).all()
        targets_by_id = {target.id: target for target in targets}
        ordered_targets = [targets_by_id[target_id] for target_id in progress.target_ids if target_id in targets_by_id]
        profile_path = ensure_profile_dir(self._settings, account_id)
        context = None
        try:
            self._update_identity_hydration_progress(progress.task_id, stage="启动浏览器")
            context, page = await self._open(profile_path, headless=True)
            chat = self._make_chat(page)
            self._update_identity_hydration_progress(progress.task_id, stage="打开会话列表")
            await chat.open_chat_page()
            self._update_identity_hydration_progress(progress.task_id, stage="检查登录状态")
            outcome = await self._confirm_session(chat, account_id)
            if outcome != SESSION_OK:
                return {
                    "overall": self.overall_for_outcome(outcome),
                    "detail": self.auth_failure_detail(outcome),
                    "results": progress.results or [],
                }

            results = list(progress.results or [])
            result_index = {item.get("target_id"): index for index, item in enumerate(results)}
            for index, target in enumerate(ordered_targets):
                self._update_identity_hydration_progress(
                    progress.task_id,
                    stage="打开好友会话并读取身份",
                    current_item=target.nickname_snapshot,
                    completed=index,
                )
                opened, match = await chat.hydrate_identity_for_label(target.nickname_snapshot)
                result = {
                    "target_id": target.id,
                    "nickname": target.nickname_snapshot,
                    "status": match.status,
                    "opened": bool(opened.opened and opened.exact),
                    "platform_user_id": match.platform_user_id,
                    "platform_handle": match.platform_handle,
                    "identity_source": match.source,
                    "identity_candidate_count": match.candidate_count,
                    "detail": match.detail,
                }
                if match.status == "bound" and match.platform_user_id:
                    bound, bind_detail = self._bind_hydrated_identity(
                        account_id,
                        target.id,
                        match.platform_user_id,
                        match.platform_handle,
                    )
                    if not bound:
                        result["status"] = "conflict"
                        result["platform_user_id"] = None
                        result["platform_handle"] = None
                        result["detail"] = bind_detail
                if target.id in result_index:
                    results[result_index[target.id]] = result
                else:
                    results.append(result)
                self._update_identity_hydration_progress(
                    progress.task_id,
                    completed=index + 1,
                    current_item=target.nickname_snapshot
                    if index + 1 < len(ordered_targets)
                    else None,
                    results=results,
                )
            self._touch_verified(account_id)
            bound_count = sum(item.get("status") == "bound" for item in results)
            unresolved_count = len(results) - bound_count
            overall = "ok" if unresolved_count == 0 else "partial"
            detail = f"补采完成：已绑定 {bound_count} 个，仍需处理 {unresolved_count} 个"
            return {"overall": overall, "detail": detail, "results": results}
        except Exception as exc:
            return {
                "overall": "error",
                "detail": type(exc).__name__,
                "results": progress.results or [],
            }
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    def identity_hydration_progress(self, account_id: str, task_id: str) -> dict:
        progress = self._identity_hydration_progress.get(task_id)
        if progress is not None:
            if progress.account_id != account_id:
                raise HTTPException(status_code=404, detail="身份补采任务不存在")
            return self._identity_hydration_progress_view(progress)
        with Session(self._engine) as session:
            task = session.get(IdentityHydrationTask, task_id)
            if task is None or task.account_id != account_id:
                raise HTTPException(status_code=404, detail="身份补采任务不存在")
            return self._identity_hydration_progress_view_from_record(task)

    async def cancel_identity_hydration(self, account_id: str, task_id: str) -> dict:
        """取消身份补采；已完成的回填保留，重复取消幂等。"""
        progress = self._identity_hydration_progress.get(task_id)
        if progress is None:
            with Session(self._engine) as session:
                task = session.get(IdentityHydrationTask, task_id)
                if task is None or task.account_id != account_id:
                    raise HTTPException(status_code=404, detail="身份补采任务不存在")
                if task.status == ScanTaskStatus.RUNNING:
                    now = _utcnow()
                    task.status = ScanTaskStatus.CANCELLED
                    task.stage = "已取消身份补采"
                    task.current_item = None
                    task.detail = "用户取消身份补采；已完成的身份回填已保留"
                    task.finished_at = now
                    task.updated_at = now
                    session.add(task)
                    session.commit()
                return self._identity_hydration_progress_view_from_record(task)

        if progress.account_id != account_id:
            raise HTTPException(status_code=404, detail="身份补采任务不存在")
        if progress.status != "running":
            return self._identity_hydration_progress_view(progress)
        task = self._identity_hydration_tasks.get(task_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if progress.status == "running":
            self._update_identity_hydration_progress(
                task_id,
                status="cancelled",
                stage="已取消身份补采",
                current_item=None,
                overall=None,
                detail="用户取消身份补采；已完成的身份回填已保留",
                results=progress.results or [],
                finished_at=_utcnow(),
            )
        return self._identity_hydration_progress_view(progress)

    def latest_identity_hydration(self, account_id: str) -> dict | None:
        """读取账号最近一次身份补采；用于刷新后恢复任务坞。"""
        with Session(self._engine) as session:
            task = session.exec(
                select(IdentityHydrationTask)
                .where(IdentityHydrationTask.account_id == account_id)
                .order_by(IdentityHydrationTask.updated_at.desc())
                .limit(1)
            ).first()
            if task is None:
                return None
            progress = self._identity_hydration_progress.get(task.id)
            if progress is not None:
                return self._identity_hydration_progress_view(progress)
            return self._identity_hydration_progress_view_from_record(task)

    def recover_persisted_scan_tasks(self) -> int:
        """启动时收束遗留扫描/身份补采；不自动重启浏览器或重复操作。"""
        now = _utcnow()
        recovered = 0
        with Session(self._engine) as session:
            tasks = session.exec(
                select(ScanTask).where(ScanTask.status == ScanTaskStatus.RUNNING)
            ).all()
            for task in tasks:
                task.status = ScanTaskStatus.INTERRUPTED
                task.stage = "后端重启，中断扫描"
                task.current_item = None
                task.detail = "后端在扫描完成前重启，已保留已发现结果；请重新发起扫描"
                task.finished_at = now
                task.updated_at = now
                session.add(task)
                recovered += 1
            hydration_tasks = session.exec(
                select(IdentityHydrationTask).where(
                    IdentityHydrationTask.status == ScanTaskStatus.RUNNING
                )
            ).all()
            for task in hydration_tasks:
                task.status = ScanTaskStatus.INTERRUPTED
                task.stage = "后端重启，中断身份补采"
                task.current_item = None
                task.detail = "后端在身份补采完成前重启，已保留已完成回填；请重新发起"
                task.finished_at = now
                task.updated_at = now
                session.add(task)
                recovered += 1
            if recovered:
                session.commit()
        return recovered

    def _make_chat(self, page) -> DouyinChat:
        return DouyinChat(
            page,
            sleep=self._sleep,
            confirm_timeout=self._confirm_timeout,
            search_timeout=self._search_timeout,
        )

    # ------------------------------------------------------------ diagnostics

    def _diagnostics_meta(self) -> dict[str, str]:
        """诊断工件的版本标识：定位改版问题时先对版本。"""
        return {
            "app_version": self._settings.version,
            "bundle_digest": bundle_digest(),
        }

    async def _capture_shot(self, chat: DouyinChat, account_id: str, label: str) -> str | None:
        """失败/未确认时的现场截图，存 artifacts/<账号>/shots/。

        截图属诊断核心功能，但绝不能因截图失败影响发送主流程：任何异常吞掉并返回 None。
        """
        page = getattr(chat, "page", None)
        if page is None:
            return None
        try:
            directory = self._settings.artifacts_dir / account_id / "shots"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{_utcnow():%Y%m%d-%H%M%S}-{label}.png"
            await page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception:
            return None

    @staticmethod
    def _page_url(chat: DouyinChat) -> str | None:
        url = getattr(getattr(chat, "page", None), "url", None)
        return url if isinstance(url, str) else None

    async def _default_network_ok(self) -> bool:
        """连通性预检：能否与抖音建立 TCP 连接（3 秒超时）。

        只探测可达性，不做任何页面/指纹层面的操作。
        """
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("www.douyin.com", 443), timeout=3.0
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

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
        """有头扫码登录：在本机弹出一个浏览器窗口给用户扫码。

        容器或纯服务器部署没有 X 显示，有头浏览器必然启动失败；该情况由
        `_login_flow` 直接给出可操作提示（`no_desktop`），不在这里拦截，避免
        让接口状态依赖运行环境。
        """
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
        return {
            "state": flow.state,
            "detail": flow.detail,
            "self_nickname": flow.self_nickname,
        }

    async def _login_flow(self, flow: LoginFlow, profile_path: Path) -> None:
        context = None
        try:
            if not headed_login_available():
                # 无 X 显示（容器/纯服务器）：有头浏览器必然启动失败，直接给出
                # 可操作的提示，而不是抛一个用户看不懂的底层异常。
                flow.state = "failed"
                flow.detail = "no_desktop"
                return
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
            flow.self_nickname = await read_self_nickname(page)
            self._mark_healthy(flow.account_id, flow.self_nickname)
        except asyncio.CancelledError:
            flow.state = "idle"
            raise
        except Exception as exc:
            flow.state = "failed"
            flow.detail = type(exc).__name__
            logger.warning("有头扫码登录流程失败：%s", exc, exc_info=True)
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            await self._notify_login_failed(flow, self._login_timeout)

    async def _notify_login_failed(self, flow: LoginFlow, timeout: float) -> None:
        """扫码登录未成功时主动回音。

        登录结果只在控制台页面上可见，而截图扫码最长要等数分钟，用户很可能已经离开；
        等下一轮自动同步再以「会话状态同步失败」告知会晚很多，所以在这里直接推送。
        `no_desktop`（在无显示环境发起有头扫码）属于用错入口，不算故障，不打扰。
        """
        if flow.state not in {"failed", "expired"} or flow.detail == "no_desktop":
            return
        if not self._notifier.enabled:
            return
        name = self._account_name(flow.account_id)
        if flow.state == "expired":
            title = "扫码登录超时"
            content = (
                f"账号「{name}」扫码登录等待超时（{int(timeout)} 秒内未完成扫码），"
                "账号仍未登录，请重新发起。"
            )
        elif flow.detail == "qr_unavailable":
            title = "扫码登录失败"
            content = (
                f"账号「{name}」未能获取登录二维码（页面既无二维码也无有效登录态），"
                "账号仍未登录；请确认抖音登录页结构是否变化后重新发起。"
            )
        else:
            title = "扫码登录失败"
            content = (
                f"账号「{name}」扫码登录失败（{flow.detail or '未知原因'}），"
                "账号仍未登录，请重新发起。"
            )
        try:
            await self._notifier.send(title, content, "timeSensitive")
        except Exception:  # noqa: BLE001 - 通知失败不影响登录流程本身
            logger.warning("登录结果通知发送失败", exc_info=True)

    async def _notify_login_verification(self, flow: LoginFlow, *, auto_sms: bool) -> None:
        """扫码后出现「身份验证」风控时尽早推送。

        这类验证要在我们浏览器的页面里选方式/输验证码，验证码又发到账号绑定
        的手机/邮箱——维护者不收到提醒，就没人去要验证码、没人来控制台操作，
        流程只会白白超时。
        """
        if not self._notifier.enabled:
            return
        name = self._account_name(flow.account_id)
        if auto_sms:
            title = "扫码登录需要身份验证"
            content = (
                f"账号「{name}」扫码后抖音要求身份验证，已自动选择"
                "「手机发送验证码」。请向账号本人索取验证码，到控制台「账号」页"
                f"登录面板输入提交（还会等待约 {int(self._qr_login_timeout)} 秒）。"
            )
        else:
            title = "扫码登录需要身份验证"
            content = (
                f"账号「{name}」扫码后抖音要求身份验证，自动选择验证方式失败。"
                "请到控制台「账号」页登录面板手动选择验证方式并输入验证码"
                f"（还会等待约 {int(self._qr_login_timeout)} 秒）。"
            )
        try:
            await self._notifier.send(title, content, "timeSensitive")
        except Exception:  # noqa: BLE001 - 通知失败不影响登录流程本身
            logger.warning("身份验证提醒发送失败", exc_info=True)

    def _account_name(self, account_id: str) -> str:
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            return account.display_name if account is not None else account_id

    def _mark_healthy(self, account_id: str, douyin_nickname: str | None = None) -> None:
        # 重新登录成功即视为会话已确认，清掉此前的连续失败计数。
        self._note_auth_ok(account_id)
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise RuntimeError("账号不存在")
            account.status = AccountStatus.HEALTHY
            account.last_verified_at = _utcnow()
            if douyin_nickname:
                account.douyin_nickname = douyin_nickname
            session.add(account)
            session.commit()

    # -------------------------------------------------------- M8-4 截图扫码登录

    async def start_qr_login(self, account_id: str) -> dict[str, str | None]:
        """headless 截图扫码登录：headless 渲染登录页（有头黑屏环境的替代路径），
        定时把二维码落盘，由前端轮询图片展示给用户扫描；登录判据与正式发送
        链路完全一致（wait_logged_in）。

        返回时状态为 `preparing`：二维码在慢网络下要数秒到数十秒才渲染出来，
        只有真正可读后才切到 `waiting_scan`，前端据此避免展示空白/过期画面。
        """
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="该账号已有登录流程进行中")
        self._qr_image_file(account_id).unlink(missing_ok=True)
        profile_path = ensure_profile_dir(self._settings, account_id)
        flow = LoginFlow(account_id=account_id, state="preparing")
        self._flows[account_id] = flow
        flow.task = asyncio.create_task(self._qr_login_flow(flow, profile_path))
        return self._login_view(flow)

    def qr_login_status(self, account_id: str) -> dict[str, str | None]:
        view = self.login_status_sync(account_id)
        view["qr_image"] = self.qr_image_path(account_id)
        return view

    def login_status_sync(self, account_id: str) -> dict[str, str | None]:
        flow = self._flows.get(account_id)
        if flow is None:
            return {"state": "idle", "detail": None}
        return self._login_view(flow)

    def _qr_image_file(self, account_id: str) -> Path:
        return self._settings.artifacts_dir / account_id / "login" / "login-qr.png"

    def qr_image_path(self, account_id: str) -> str | None:
        flow = self._flows.get(account_id)
        if flow is not None and flow.qr_image:
            return flow.qr_image
        existing = self._qr_image_file(account_id)
        return str(existing) if existing.exists() else None

    async def verification_options(self, account_id: str) -> dict:
        """读取「身份验证」弹窗当前可见的验证方式（仅验证等待中的流程可用）。"""
        flow = self._active_flow(account_id)
        if flow is None or flow.chat is None or flow.state != "verification_required":
            raise LookupError("当前没有等待身份验证的登录流程")
        options = await flow.chat.verification_options()
        return {
            "state": flow.state,
            "detail": flow.detail,
            "options": options,
            "auto_option": flow.auto_option,
        }

    async def click_verification_option(self, account_id: str, option: str) -> dict:
        """在「身份验证」弹窗里点选某个验证方式（按可见文案匹配）。"""
        flow = self._active_flow(account_id)
        if flow is None or flow.chat is None or flow.state != "verification_required":
            raise LookupError("当前没有等待身份验证的登录流程")
        clicked = await flow.chat.click_verification_option(option)
        if not clicked:
            raise ValueError(f"页面上没有「{option}」这个验证方式，请对照截图确认")
        flow.detail = f"已选择「{option}」；验证码发出后请在下方输入并提交。"
        return {"ok": True, "state": flow.state, "detail": flow.detail}

    async def submit_verification_code(self, account_id: str, code: str) -> dict:
        """把验证码填进弹窗输入框并提交（通用驱动，失败如实上报）。"""
        flow = self._active_flow(account_id)
        if flow is None or flow.chat is None or flow.state != "verification_required":
            raise LookupError("当前没有等待身份验证的登录流程")
        submitted = await flow.chat.submit_verification_code(code)
        if not submitted:
            raise ValueError("未能在页面上找到验证码输入框或提交按钮，请对照截图确认")
        flow.detail = "验证码已提交，等待登录完成…"
        return {"ok": True, "state": flow.state, "detail": flow.detail}

    async def _wait_for_qr_or_session(self, page, chat) -> str:
        """轮询等待二维码真正可读，或确认已有登录态；慢网络下要数秒到数十秒。

        抖音在 Profile 已持有有效会话时不会再展示登录二维码。只等二维码的话，
        这类账号会一直空等到 qr_unavailable——「截图扫码」对它们永远失败。因此
        每轮同时判断当前是否已在登录态，命中即按登录成功处理。

        按轮次计数而非墙上时间：轮次 × 间隔等于超时预算，既避免依赖 sleep 的
        真实时长，也让测试可以注入空 sleep 后仍然确定性地结束。

        返回 "qr"（二维码可读）/ "session"（已有登录态）/ "none"（都没等到）。
        """
        is_closed = getattr(page, "is_closed", None)
        for _ in range(int(QR_PREPARE_TIMEOUT / QR_PREPARE_POLL_INTERVAL)):
            if callable(is_closed) and is_closed():
                return "none"
            if await read_qr_data_url(page) is not None:
                return "qr"
            try:
                if await chat.is_logged_in():
                    return "session"
            except Exception:  # noqa: BLE001 - 轮询判据，瞬时错误按「尚未登录」重试
                pass
            await self._sleep(QR_PREPARE_POLL_INTERVAL)
        return "none"

    async def _qr_login_flow(
        self, flow: LoginFlow, profile_path: Path
    ) -> None:
        context = None
        page = None
        try:
            # 先等到二维码真的渲染出来再对外暴露 waiting_scan：慢网络（例如服务器
            # 访问国内 CDN）下弹窗和二维码要数秒到数十秒才出现，提前进入等待只会
            # 让前端拿到 404 或骨架屏截图。
            flow.state = "preparing"
            context, page = await self._open(profile_path, headless=True)
            chat = DouyinChat(page, sleep=self._sleep)
            flow.chat = chat
            await chat.open_chat_page()

            directory = self._qr_image_file(flow.account_id).parent
            target_path = self._qr_image_file(flow.account_id)

            async def take_qr_shot() -> None:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    # 优先：直接取二维码本体的 data URL（像素级清晰，可扫性最佳）
                    data_url = await read_qr_data_url(page)
                    if data_url:
                        header, _, payload = data_url.partition(",")
                        if "image/png" in header and payload:
                            target_path.write_bytes(base64.b64decode(payload))
                            flow.qr_image = str(target_path)
                            return
                    # 兜底：整页截图（二维码较小但保底可见）
                    await page.screenshot(path=str(target_path), full_page=False)
                    flow.qr_image = str(target_path)
                except Exception:
                    pass  # 截图失败不影响登录检测

            outcome = await self._wait_for_qr_or_session(page, chat)
            if outcome == "session":
                # 已有有效登录态：抖音不再展示二维码，直接按登录成功处理。
                flow.state = "succeeded"
                flow.self_nickname = await read_self_nickname(page)
                self._mark_healthy(flow.account_id, flow.self_nickname)
                return
            if outcome == "none":
                flow.state = "failed"
                flow.detail = "qr_unavailable"
                return

            # 先同步截一帧：保证「等待扫码」期间二维码必然已可展示
            await take_qr_shot()
            flow.state = "waiting_scan"

            async def shoot() -> None:
                while True:
                    await take_qr_shot()
                    await self._sleep(self._qr_screenshot_interval)

            verification_seen = False

            async def watch_verification() -> None:
                """并行盯「身份验证」风控弹窗：出现就自动选短信验证并推送提醒。

                扫码人手机上点完登录就没有后续了，验证全部由维护者在控制台
                完成：这里默认自动点选「手机发送验证码」（点选失败会在后续
                轮询里重试，最多 3 次），维护者只需索取验证码并输入。
                不改主等待循环的判据（登录与否仍只看会话列表）。
                """
                nonlocal verification_seen
                click_attempts = 0
                while True:
                    await self._sleep(5.0)
                    try:
                        if not await chat.is_verification_required():
                            continue
                    except Exception:  # noqa: BLE001 - 检测失败按未出现处理
                        continue
                    if not verification_seen:
                        verification_seen = True
                        flow.state = "verification_required"
                        flow.detail = LOGIN_VERIFICATION_GUIDANCE
                        logger.warning(
                            "截图扫码出现「身份验证」风控（账号 %s），延长等待并提醒",
                            flow.account_id,
                        )
                    if flow.auto_option is None and click_attempts < 3:
                        click_attempts += 1
                        picked = await chat.click_verification_option(
                            LOGIN_VERIFICATION_AUTO_OPTION
                        )
                        if picked:
                            flow.auto_option = LOGIN_VERIFICATION_AUTO_OPTION
                            flow.detail = LOGIN_VERIFICATION_SMS_PENDING
                            logger.warning(
                                "已自动选择「%s」（账号 %s），等待维护者输入验证码",
                                LOGIN_VERIFICATION_AUTO_OPTION,
                                flow.account_id,
                            )
                            await self._notify_login_verification(flow, auto_sms=True)
                        elif click_attempts >= 3:
                            logger.warning(
                                "自动选择验证方式失败，转手动（账号 %s）", flow.account_id
                            )
                            await self._notify_login_verification(flow, auto_sms=False)

            shooter = asyncio.create_task(shoot())
            verifier = asyncio.create_task(watch_verification())
            try:
                logged_in = await chat.wait_logged_in(
                    timeout=self._qr_login_timeout,
                    poll_interval=self._login_poll_interval,
                )
                if not logged_in and verification_seen:
                    # 「身份验证」多数要账号本人在抖音 App 里确认授权，那是人干
                    # 的时间；检测到弹窗就再给一轮完整窗口，而不是立即判超时。
                    logger.warning(
                        "身份验证等待中，追加一轮等待（账号 %s）", flow.account_id
                    )
                    logged_in = await chat.wait_logged_in(
                        timeout=self._qr_login_timeout,
                        poll_interval=self._login_poll_interval,
                    )
            finally:
                shooter.cancel()
                verifier.cancel()
            if not logged_in:
                flow.state = "expired"
                flow.detail = (
                    "等待身份验证/扫码超时，请重新发起"
                    if verification_seen
                    else "等待扫码超时，请重新发起"
                )
                logger.warning(
                    "截图扫码等待超时；流程结束时页面 %s", getattr(page, "url", "unknown")
                )
                return
            flow.state = "succeeded"
            flow.self_nickname = await read_self_nickname(page)
            self._mark_healthy(flow.account_id, flow.self_nickname)
        except asyncio.CancelledError:
            flow.state = "idle"
            raise
        except Exception as exc:
            flow.state = "failed"
            flow.detail = type(exc).__name__
            logger.warning("截图扫码登录流程失败：%s", exc, exc_info=True)
        finally:
            flow.chat = None
            if context is not None:
                if page is not None and flow.state in {"failed", "expired"}:
                    # 失败现场留一张截图，便于判断是风控页、验证码还是页面结构变化
                    try:
                        diagnostic = self._qr_image_file(flow.account_id).parent / "login-final.png"
                        await page.screenshot(path=str(diagnostic), full_page=False)
                        logger.warning("登录未完成（%s），现场截图：%s", flow.state, diagnostic)
                    except Exception:
                        pass
                try:
                    await context.close()
                except Exception:
                    pass
            await self._notify_login_failed(flow, self._qr_login_timeout)

    async def _confirm_session(self, chat, account_id: str) -> str:
        """登录判定（四个入口共用，口径必须一致）。

        通过则清除连续失败计数；失败则区分两类原因再决定后果：

        - 页面根本没加载出来（Chromium 错误页）→ `SESSION_NETWORK_PROBLEM`：既不计入
          判定失败，也不改账号状态，由调用方按网络问题处理；
        - 页面正常但看不到会话列表 → 走容忍计数（`SESSION_AUTH_EXPIRED` /
          `SESSION_AUTH_UNCONFIRMED`），并留一张现场截图便于事后分辨「确实登出」与
          「慢/验证页/风控」。

        区分这两类是必要的：原实现把两者一律当「登录失效」，于是网络抖动会以
        「请重新扫码」的面目出现（2026-10-10 实测的误报即属此类可能之一）。
        """
        if await chat.wait_logged_in(
            timeout=self._login_check_timeout,
            poll_interval=self._login_poll_interval,
        ):
            self._note_auth_ok(account_id)
            return SESSION_OK

        page_url = self._page_url(chat)
        if self._looks_like_load_failure(page_url):
            logger.warning(
                "账号 %s 登录判定失败，但页面未正常加载（url=%s），按网络异常处理、不计入失效判定",
                account_id,
                page_url,
            )
            return SESSION_NETWORK_PROBLEM

        # 留现场：截图 + 页面 URL。没有它只能靠猜是「慢」「验证页」还是真登出。
        evidence = await self._capture_shot(chat, account_id, "auth_check_failed")
        self._auth_evidence[account_id] = {"page_url": page_url, "screenshot": evidence}
        logger.warning(
            "账号 %s 登录判定失败（url=%s，现场截图=%s）", account_id, page_url, evidence
        )

        marked = self._mark_auth_expired(account_id)
        return SESSION_AUTH_EXPIRED if marked else SESSION_AUTH_UNCONFIRMED

    @staticmethod
    def _looks_like_load_failure(page_url: str | None) -> bool:
        """页面是否根本没加载成功。

        只认明确的失败标志（Chromium 错误页 / 空白页）：宁可把可疑情况按「登录失效」
        处理走容忍计数，也不要把真的登出误判成网络问题——后者会让账号永远不被标记、
        静默失败下去。
        """
        if not page_url:
            return False
        return page_url.startswith(_LOAD_FAILURE_URL_PREFIXES)

    @staticmethod
    def auth_failure_detail(outcome: str) -> str:
        """登录判定失败时给用户看的说明（务必与后果一致，别让人白跑一趟扫码）。"""
        if outcome == SESSION_NETWORK_PROBLEM:
            return "抖音页面未能加载（网络异常），本次未判定登录状态、账号未被标记失效，会自动重试"
        if outcome == SESSION_AUTH_EXPIRED:
            return "登录已失效，请重新扫码"
        return "登录判定未通过，但未确认失效（可能是瞬时抖动）；账号未被标记失效，会自动重试"

    @staticmethod
    def overall_for_outcome(outcome: str, *, network_overall: str = "error") -> str:
        """把登录判定结论映射到响应里的 overall。

        `Literal` 契约不允许新增取值，因此网络问题只能落到已有的值上：
        发送链路用 `network_unavailable`（调度器据此延迟重试、不消耗尝试次数），
        其余链路（扫描/定位校验/身份补采）用 `error`。
        """
        if outcome == SESSION_NETWORK_PROBLEM:
            return network_overall
        return "auth_expired"

    def _mark_auth_expired(self, account_id: str) -> bool:
        """记录一次登录判定失败；连续失败达到容忍次数才真正标记失效，返回是否已标记。

        调度器对非 HEALTHY 账号会同时跳过扫描与发送，若单次抖动就落终局，账号会
        停摆且无法自愈（只能人工重新扫码）。连续两次才标记：既挡住抖动，也保证
        真失效时仍会被判定并提醒。
        """
        count = self._auth_check_failures.get(account_id, 0) + 1
        self._auth_check_failures[account_id] = count
        if count < self._auth_check_tolerance:
            logger.warning(
                "账号 %s 登录判定失败第 %d/%d 次，暂不标记失效（避免单次抖动锁死调度）",
                account_id,
                count,
                self._auth_check_tolerance,
            )
            return False
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is not None:
                account.status = AccountStatus.AUTH_EXPIRED
                session.add(account)
                session.commit()
        logger.warning(
            "账号 %s 登录判定连续失败 %d 次，已标记登录失效",
            account_id,
            count,
        )
        return True

    def _note_auth_ok(self, account_id: str) -> None:
        """登录判定通过：清除连续失败计数，让偶发抖动不会累积成终局判定。"""
        self._auth_check_failures.pop(account_id, None)
        self._auth_evidence.pop(account_id, None)

    def _touch_verified(self, account_id: str) -> None:
        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is not None:
                account.last_verified_at = _utcnow()
                session.add(account)
                session.commit()

    # ------------------------------------------------------------ dry run

    async def _scan_chats_impl(
        self,
        account_id: str,
        progress: ScanProgress | None = None,
    ) -> dict:
        """执行一次全量扫描；progress 存在时把真实扫描阶段写入内存快照。"""
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能扫描会话")
        if progress is None and self._active_scan(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号已有会话扫描进行中")
        send_progress = self._send_progress.get(account_id)
        if send_progress is not None and send_progress.status == "running":
            raise HTTPException(status_code=409, detail="账号正在发送，暂不能扫描会话")

        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="账号不存在")

        def update_progress(**changes: object) -> None:
            if progress is not None:
                self._update_scan_progress(progress.task_id, **changes)

        async def on_scan_progress(
            count: int,
            round_no: int,
            current_item: str | None,
            entries: list[dict],
        ) -> None:
            update_progress(
                stage="滚动读取会话",
                completed=count,
                current_item=current_item,
                round=round_no,
                entries=entries,
            )

        profile_path = ensure_profile_dir(self._settings, account_id)
        context = None
        try:
            update_progress(stage="启动浏览器")
            context, page = await self._open(profile_path, headless=True)
            chat = self._make_chat(page)
            update_progress(stage="打开会话列表")
            await chat.open_chat_page()
            update_progress(stage="检查登录状态")
            outcome = await self._confirm_session(chat, account_id)
            if outcome != SESSION_OK:
                return {
                    "overall": self.overall_for_outcome(outcome),
                    "detail": self.auth_failure_detail(outcome),
                    "entries": [],
                }
            try:
                update_progress(stage="滚动读取会话", current_item=None)
                entries = await chat.scan_all_chats(
                    progress_callback=on_scan_progress if progress is not None else None,
                )
            except Exception as exc:
                return {"overall": "error", "detail": type(exc).__name__, "entries": []}
            self._touch_verified(account_id)
            return {"overall": "ok", "detail": None, "entries": entries}
        except Exception as exc:
            return {"overall": "error", "detail": type(exc).__name__, "entries": []}
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    async def scan_chats(self, account_id: str) -> dict:
        """兼容旧客户端的同步扫描接口；新控制台使用 start/progress 接口。"""
        return await self._scan_chats_impl(account_id)

    @staticmethod
    def _scan_entry_matches_target(entry: dict, target: ContractTarget) -> bool:
        return (
            match_scan_entry_to_target(
                entry,
                [
                    {
                        "targetId": target.id,
                        "title": target.nickname_snapshot,
                        "conversationType": target.conversation_type,
                        "platformUserId": target.platform_user_id,
                        "platformConversationId": target.platform_conversation_id,
                    }
                ],
            )
            is not None
        )

    @staticmethod
    def _spark_label_for_scan_entry(entry: dict) -> str | None:
        label = entry.get("streak_label")
        if isinstance(label, str) and label.strip():
            return label.strip()
        if entry.get("streak_days") is not None:
            return None
        preview = entry.get("preview")
        if isinstance(preview, str) and "火花" in preview:
            return preview.strip()[:64]
        return "无火花"

    def _sync_existing_scan_entries(self, account_id: str, entries: list[dict]) -> dict:
        """自动扫描只刷新已有契约对象，绝不把新会话静默导入好友列表。"""
        with Session(self._engine) as session:
            targets = session.exec(
                select(ContractTarget).where(
                    ContractTarget.account_id == account_id,
                    ContractTarget.deleted_at.is_(None),
                )
            ).all()
            items: list[BatchTargetItem] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                title = entry.get("title")
                if not isinstance(title, str) or not any(
                    self._scan_entry_matches_target(entry, target) for target in targets
                ):
                    continue
                items.append(
                    BatchTargetItem(
                        nickname=title,
                        platform_user_id=entry.get("platform_user_id"),
                        platform_handle=entry.get("platform_handle"),
                        platform_conversation_id=entry.get("platform_conversation_id"),
                        conversation_type=entry.get("conversation_type"),
                        identity_status=entry.get("identity_status") or "not_observed",
                        conversation_status=entry.get("conversation_status") or "not_observed",
                        streak_days=entry.get("streak_days"),
                        spark_days=entry.get("streak_days"),
                        spark_kind=entry.get("streak_kind"),
                        spark_label=self._spark_label_for_scan_entry(entry),
                    )
                )
            if not items:
                return {"refreshed": [], "created": []}
            account_obj = session.get(Account, account_id)
            result = create_targets_batch(
                session,
                TargetBatchCreate(account_id=account_id, items=items),
                user_id=account_obj.user_id if account_obj is not None else None,
            )
            return result.model_dump()

    async def run_auto_scan(self, account_id: str) -> dict:
        """执行一次由调度器触发的扫描，并在完成后回写已有对象的官方快照。"""
        initial = await self.start_scan_chats(account_id, source="automatic")
        task_id = initial["task_id"]
        progress = initial
        while progress["status"] == "running":
            await self._sleep(0.5)
            progress = self.scan_chats_progress(account_id, task_id)
        if progress["status"] != "completed":
            return {
                "overall": progress.get("overall") or "error",
                "detail": progress.get("detail"),
                "entries": progress.get("entries") or [],
            }
        try:
            sync = self._sync_existing_scan_entries(
                account_id,
                progress.get("entries") or [],
            )
        except Exception as exc:
            detail = f"扫描完成，但官方状态同步失败：{type(exc).__name__}"
            with Session(self._engine) as session:
                task = session.get(ScanTask, task_id)
                if task is not None:
                    task.detail = detail
                    task.updated_at = _utcnow()
                    session.add(task)
                    session.commit()
            return {
                "overall": "error",
                "detail": detail,
                "entries": progress.get("entries") or [],
            }
        return {
            "overall": "ok",
            "detail": None,
            "entries": progress.get("entries") or [],
            "sync": sync,
        }

    async def run_dry_run(self, account_id: str) -> dict:
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能执行 Dry Run")
        if self._active_scan(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在扫描会话，暂不能执行 Dry Run")

        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="账号不存在")
            targets = session.exec(
                select(ContractTarget).where(
                    ContractTarget.account_id == account_id,
                    ContractTarget.deleted_at.is_(None),
                )
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
            outcome = await self._confirm_session(chat, account_id)
            if outcome != SESSION_OK:
                overall = self.overall_for_outcome(outcome)
                detail = self.auth_failure_detail(outcome)
            else:
                try:
                    await chat.warm_up_list()
                except Exception:
                    pass
                visible_titles = await chat.visible_titles()
                for target in targets:
                    opened = await chat.open_conversation(
                        target.nickname_snapshot,
                        target.platform_user_id,
                        target.platform_conversation_id,
                    )
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
                                target.nickname_snapshot,
                                "ambiguous",
                                opened.title,
                                (
                                    "已打开会话，但稳定抖音身份无法核对，已阻止发送"
                                    if target.platform_user_id or target.platform_conversation_id
                                    else None
                                ),
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
            "meta": self._diagnostics_meta(),
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

    async def run_send(
        self,
        account_id: str,
        *,
        force: bool = False,
        exclude_scheduled: bool = False,
        force_run_id: str | None = None,
    ) -> dict:
        """对账号批量发送。手动「立即发送/强制发送全部」包含全部启用好友；
        自动账号级调度传 exclude_scheduled=True，只发「未设置独立发送时刻」的好友
        （这些好友由 scheduler 按各自精确时刻单独派发）。

        force_run_id 供调度器传「按业务日固定」的槽位后缀：开启 scheduled_always_send
        后定时任务忽略当天已发送记录再发一遍，而重试复用同一槽位不会重复发送。
        """
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能发送")
        if self._active_scan(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在扫描会话，暂不能发送")
        existing_progress = self._send_progress.get(account_id)
        if existing_progress is not None and existing_progress.status == "running":
            raise HTTPException(status_code=409, detail="账号已有发送任务进行中")

        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="账号不存在")
            if not account.enabled:
                raise HTTPException(status_code=409, detail="账号已暂停")
            query = (
                select(ContractTarget)
                .where(
                    ContractTarget.account_id == account_id,
                    ContractTarget.enabled,
                    ContractTarget.deleted_at.is_(None),
                )
            )
            if exclude_scheduled:
                # 自动账号级批量：剔除已设置独立精确发送时刻的好友（他们各自到点单独发）
                query = query.where(ContractTarget.send_time_hhmm.is_(None))
            targets = session.exec(query).all()
        if not targets:
            if exclude_scheduled:
                # 自动账号批量为空：通常意味着全部好友都设置了独立时刻，由各自派发处理
                return {
                    "account_id": account_id,
                    "overall": "ok",
                    "detail": "全部好友设置独立发送时刻，账号批量无需发送",
                    "results": [],
                    "artifact_path": None,
                }
            raise HTTPException(status_code=409, detail="没有启用中的好友，请先到好友页添加")

        self._start_send_progress(account_id, len(targets))
        blocked = await self._preflight_network(account_id)
        if blocked is not None:
            return blocked

        force_run_id = force_run_id or (uuid4().hex[:12] if force else None)
        return await self._execute_send(account, targets, force_run_id=force_run_id)

    async def run_send_for_targets(
        self, account_id: str, target_ids: list[str], *, force_run_id: str | None = None
    ) -> dict:
        """对指定好友集发送（自动按好友精确时刻派发用）。

        force_run_id 由调度器在开启 scheduled_always_send 时传入「按业务日固定」的槽位
        后缀，使同一好友当天已发过也照发一遍，而重试复用同一槽位不会重复发送。
        """
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能发送")
        if self._active_scan(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在扫描会话，暂不能发送")
        existing_progress = self._send_progress.get(account_id)
        if existing_progress is not None and existing_progress.status == "running":
            raise HTTPException(status_code=409, detail="账号已有发送任务进行中")

        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="账号不存在")
            if not account.enabled:
                raise HTTPException(status_code=409, detail="账号已暂停")
            targets = session.exec(
                select(ContractTarget).where(
                    ContractTarget.account_id == account_id,
                    ContractTarget.id.in_(target_ids),
                    ContractTarget.enabled,
                    ContractTarget.deleted_at.is_(None),
                )
            ).all()
        if not targets:
            raise HTTPException(status_code=409, detail="没有可发送的好友")

        self._start_send_progress(account_id, len(targets))
        blocked = await self._preflight_network(account_id)
        if blocked is not None:
            return blocked

        return await self._execute_send(account, targets, force_run_id=force_run_id)

    async def send_target(
        self,
        account_id: str,
        target_id: str,
        *,
        template_id: str | None = None,
        content: str | None = None,
    ) -> dict:
        """对单个好友强制发送：可指定模板或直接输入文本（二选一），始终绕过当天成功记录。"""
        if self._active_flow(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在登录流程中，暂不能发送")
        if self._active_scan(account_id) is not None:
            raise HTTPException(status_code=409, detail="账号正在扫描会话，暂不能发送")
        existing_progress = self._send_progress.get(account_id)
        if existing_progress is not None and existing_progress.status == "running":
            raise HTTPException(status_code=409, detail="账号已有发送任务进行中")

        with Session(self._engine) as session:
            account = session.get(Account, account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="账号不存在")
            if not account.enabled:
                raise HTTPException(status_code=409, detail="账号已暂停")
            target = session.get(ContractTarget, target_id)
            if target is None or target.account_id != account_id:
                raise HTTPException(status_code=404, detail="好友不存在")
            if target.deleted_at is not None:
                raise HTTPException(status_code=404, detail="好友不存在")
            if not target.enabled:
                raise HTTPException(status_code=409, detail="好友已暂停，不能发送")

        self._start_send_progress(account_id, 1)
        blocked = await self._preflight_network(account_id)
        if blocked is not None:
            return blocked

        force_run_id = uuid4().hex[:12]
        return await self._execute_send(
            account,
            [target],
            force_run_id=force_run_id,
            template_id_override=template_id,
            content_override=content,
        )

    async def _preflight_network(self, account_id: str) -> dict | None:
        """M6-2 网络预检：离线时不开浏览器、不消耗任何当日任务。返回响应体或 None（可继续）。"""
        self._update_send_progress(account_id, stage="检查网络")
        checker = self._network_checker or self._default_network_ok
        try:
            network_ok = await checker()
        except Exception as exc:
            self._finish_send_progress(
                account_id,
                status="failed",
                completed=0,
                stage="网络检查失败",
                overall="error",
                detail=type(exc).__name__,
            )
            raise
        if not network_ok:
            self._finish_send_progress(
                account_id,
                status="failed",
                completed=0,
                stage="网络不可用",
                overall="network_unavailable",
                detail="网络不可用，本轮未开始",
            )
            return {
                "account_id": account_id,
                "overall": "network_unavailable",
                "detail": "网络不可用，本轮未开始",
                "results": [],
                "artifact_path": None,
            }
        return None

    async def _execute_send(
        self,
        account: Account,
        targets: list[ContractTarget],
        *,
        force_run_id: str | None = None,
        template_id_override: str | None = None,
        content_override: str | None = None,
    ) -> dict:
        """打开浏览器 + 登录检查 + 逐条发送 + 工件落盘。批量 run_send 与单好友 send_target 共用。"""
        account_id = account.id
        profile_path = ensure_profile_dir(self._settings, account_id)
        results: list[dict] = []
        overall = "ok"
        detail: str | None = None
        context = None
        diagnostics: dict | None = None
        try:
            self._update_send_progress(account_id, stage="启动浏览器")
            context, page = await self._open(profile_path, headless=True)
            chat = self._make_chat(page)
            await chat.open_chat_page()
            self._update_send_progress(account_id, stage="检查登录状态")
            outcome = await self._confirm_session(chat, account_id)
            if outcome != SESSION_OK:
                overall = self.overall_for_outcome(
                    outcome, network_overall="network_unavailable"
                )
                detail = self.auth_failure_detail(outcome)
                # 判定失败已在 _confirm_session 里留过现场，这里复用同一张截图，
                # 不再重复截图（send 链路额外需要把证据写进工件 JSON）。
                evidence = self._auth_evidence.get(account_id) or {}
                diagnostics = {
                    "stage": outcome,
                    "confirmed": outcome == SESSION_AUTH_EXPIRED,
                    "screenshot": evidence.get("screenshot"),
                    "page_url": evidence.get("page_url") or self._page_url(chat),
                }
                self._finish_send_progress(
                    account_id,
                    status="failed",
                    completed=0,
                    stage={
                        SESSION_AUTH_EXPIRED: "登录失效",
                        SESSION_NETWORK_PROBLEM: "网络异常",
                    }.get(outcome, "登录判定未通过"),
                    overall=overall,
                    detail=detail,
                )
            else:
                business_date = datetime.now(ZoneInfo(account.timezone)).date()
                self._update_send_progress(account_id, stage="定位好友")
                for index, target in enumerate(targets):
                    self._update_send_progress(
                        account_id,
                        completed=index,
                        current_target=target.nickname_snapshot,
                        stage="冷却中" if index > 0 and self._send_cooldown > 0 else "定位并发送",
                    )
                    # M6-2 发送冷却：好友之间留出随机间隔，降低频率风险（首轮不等待）。
                    if index > 0 and self._send_cooldown > 0:
                        await self._sleep(self._cooldown_delay())
                        self._update_send_progress(
                            account_id,
                            stage="定位并发送",
                        )
                    result = await self._send_one(
                        chat,
                        account,
                        target,
                        business_date,
                        force_run_id=force_run_id,
                        template_id_override=template_id_override,
                        content_override=content_override,
                    )
                    results.append(result)
                    self._update_send_progress(
                        account_id,
                        completed=index + 1,
                        last_result=result["status"],
                        stage="已处理",
                    )
                    if result["status"] not in ("succeeded", "duplicate_succeeded", "skipped"):
                        overall = "partial"
        except Exception as exc:
            overall = "error"
            detail = type(exc).__name__
            self._finish_send_progress(
                account_id,
                status="failed",
                completed=len(results),
                stage="流程中断",
                overall=overall,
                detail=detail,
            )
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

        artifact_path = self._write_send_artifact(
            account_id, overall, detail, results, diagnostics=diagnostics
        )
        if overall in ("ok", "partial"):
            self._finish_send_progress(
                account_id,
                status="completed",
                completed=len(targets),
                stage="发送完成",
                overall=overall,
                detail=detail,
            )
        elif overall != "auth_expired":
            # auth_expired 已在上面的诊断分支记录；其它终态也要让任务坞保持可见。
            self._finish_send_progress(
                account_id,
                status="failed",
                completed=len(results),
                stage="发送结束",
                overall=overall,
                detail=detail,
            )
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
        *,
        force_run_id: str | None = None,
        template_id_override: str | None = None,
        content_override: str | None = None,
    ) -> dict:
        daily_jobs: list[SendJob] = []
        with Session(self._engine) as session:
            current = session.get(ContractTarget, target.id)
            if current is None or not current.enabled or current.deleted_at is not None:
                return {"target": target.nickname_snapshot, "status": "skipped",
                        "detail": "好友已删除或暂停", "failure_class": None, "message": None}
            nickname = current.nickname_snapshot
            platform_user_id = current.platform_user_id
            platform_conversation_id = current.platform_conversation_id
            streak_days = current.streak_days
            # 单好友手动发送可直接指定模板或输入文本；否则沿用好友当前绑定的模板。
            effective_template_id = template_id_override or current.template_id
            template = (
                session.get(MessageTemplate, effective_template_id)
                if effective_template_id
                else None
            )
            # content_override 模式：直接输入文本（不依赖模板）。
            raw_text = content_override is not None
            template_ok = (not raw_text) and template is not None and template.enabled
            template_id = template.id if template_ok else (None if raw_text else current.template_id)
            template_version = template.version if template_ok else None
            template_type = template.type if template_ok else TemplateType.TEXT
            template_content = template.content if template_ok else None
            image_path = template.image_path if template_ok else None
            sticker_name = template.sticker_name if template_ok else None
            sticker_label = template.sticker_label if template_ok else None
            sticker_category = template.sticker_category if template_ok else None
            sticker_fallback_index = template.sticker_fallback_index if template_ok else None
            if template_ok or raw_text:
                daily_jobs = session.exec(
                    select(SendJob).where(
                        SendJob.account_id == account.id,
                        SendJob.target_id == target.id,
                        SendJob.business_date == business_date,
                    )
                ).all()
            pool_item = None
            pool_content = None
            if template_ok and template.type == TemplateType.POOL:
                # M8-1 文案池：按账号本地星期选候选并记 LRU（发送即消耗轮换顺序）
                weekday_index = datetime.now(ZoneInfo(account.timezone)).weekday()
                pool_item = pick_pool_item(session, template.id, weekday_index, consume=True)
                pool_content = pool_item.content if pool_item is not None else None

        if not template_ok and not raw_text:
            return {"target": nickname, "status": "skipped",
                    "detail": "未绑定可用模板", "failure_class": None, "message": None}
        if any(job.status == JobStatus.UNKNOWN_REQUIRES_REVIEW for job in daily_jobs):
            return {
                "target": nickname,
                "status": "duplicate_unknown",
                "detail": "今日已有结果未确认，强制发送仍跳过以防重复",
                "failure_class": None,
                "message": None,
            }
        if template_type == TemplateType.IMAGE and not raw_text and not image_path:
            return {"target": nickname, "status": "skipped",
                    "detail": "图片模板缺少图片路径", "failure_class": None, "message": None}
        if template_type == TemplateType.STICKER and not raw_text and not sticker_name:
            return {"target": nickname, "status": "skipped",
                    "detail": "表情模板缺少表情名称", "failure_class": None, "message": None}
        if template_type == TemplateType.POOL and not raw_text and pool_item is None:
            return {"target": nickname, "status": "skipped",
                    "detail": "文案池今日无可用候选", "failure_class": None, "message": None}

        message = None
        if raw_text:
            message = render_message(
                content_override,
                target_name=nickname,
                streak_days=streak_days,
                timezone=account.timezone,
            )
        elif template_type == TemplateType.TEXT:
            message = render_message(
                template_content,
                target_name=nickname,
                streak_days=streak_days,
                timezone=account.timezone,
            )
        elif template_type == TemplateType.POOL and pool_content is not None:
            message = render_message(
                pool_content,
                target_name=nickname,
                streak_days=streak_days,
                timezone=account.timezone,
            )
        if raw_text:
            # 直接输入的文本用内容哈希做槽位命名空间，避免过长（槽位上限 64 字符）。
            digest = hashlib.sha1(content_override.encode("utf-8")).hexdigest()[:12]
            base_slot = f"raw:{digest}"
        else:
            base_slot = f"{template_id}:v{template_version}"
        if force_run_id is None and any(
            job.status == JobStatus.SUCCEEDED
            and job.message_slot_id.startswith(f"{base_slot}:force:")
            for job in daily_jobs
        ):
            return {
                "target": nickname,
                "status": "duplicate_succeeded",
                "detail": "今日已通过手动强制发送，已跳过",
                "failure_class": None,
                "message": message,
            }
        slot = base_slot
        if force_run_id is not None:
            # 保留每日任务表的可审计记录，同时不与普通发送的成功槽位冲突。
            slot = f"{slot}:force:{force_run_id}"

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

        opened = await chat.open_conversation(
            nickname,
            platform_user_id,
            platform_conversation_id,
        )
        if not opened.opened:
            screenshot = await self._capture_shot(chat, account.id, "target_not_found")
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.FAILED_TERMINAL, failure=FailureClass.TARGET_UNCERTAIN,
                confirmed=False,
            )
            return {"target": nickname, "status": "not_found",
                    "failure_class": FailureClass.TARGET_UNCERTAIN.value,
                    "detail": "会话列表中未找到该好友", "message": message,
                    "screenshot": screenshot, "page_url": self._page_url(chat)}
        if not opened.exact:
            screenshot = await self._capture_shot(chat, account.id, "target_ambiguous")
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.FAILED_TERMINAL, failure=FailureClass.TARGET_UNCERTAIN,
                confirmed=False,
            )
            detail = (
                "已打开会话，但稳定抖音身份无法核对，已阻止发送"
                if platform_user_id or platform_conversation_id
                else f"右侧标题为「{opened.title}」，与好友名不一致"
            )
            return {"target": nickname, "status": "ambiguous",
                    "failure_class": FailureClass.TARGET_UNCERTAIN.value,
                    "detail": detail,
                    "message": message,
                    "screenshot": screenshot, "page_url": self._page_url(chat)}

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
        except SendOutcomeUnknownError as exc:
            screenshot = await self._capture_shot(chat, account.id, "unknown_outcome")
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.UNKNOWN_REQUIRES_REVIEW,
                failure=FailureClass.UNKNOWN_OUTCOME,
                confirmed=False,
                attempt_no=attempt_no,
            )
            return {"target": nickname, "status": "unknown_requires_review",
                    "failure_class": FailureClass.UNKNOWN_OUTCOME.value,
                    "detail": str(exc) or "已触发发送但结果未确认，已阻止自动重试",
                    "message": message,
                    "screenshot": screenshot, "page_url": self._page_url(chat)}
        except DomChangedError as exc:
            screenshot = await self._capture_shot(chat, account.id, "dom_changed")
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.FAILED_RETRYABLE, failure=FailureClass.DOM_CHANGED,
                confirmed=False, attempt_no=attempt_no,
            )
            return {"target": nickname, "status": "failed_retryable",
                    "failure_class": FailureClass.DOM_CHANGED.value,
                    "detail": str(exc) or "页面结构变化", "message": message,
                    "screenshot": screenshot, "page_url": self._page_url(chat)}
        except Exception as exc:
            screenshot = await self._capture_shot(chat, account.id, "send_error")
            self._finalize_send(
                job.id, target.id, account.id,
                status=JobStatus.FAILED_RETRYABLE, failure=FailureClass.OTHER,
                confirmed=False, attempt_no=attempt_no,
            )
            return {"target": nickname, "status": "failed_retryable",
                    "failure_class": FailureClass.OTHER.value,
                    "detail": type(exc).__name__, "message": message,
                    "screenshot": screenshot, "page_url": self._page_url(chat)}

        self._update_job_status(job.id, JobStatus.CONFIRMING)
        outcome = await self._confirm_send(chat, template_type)

        screenshot: str | None = None
        if outcome != "succeeded":
            label = "unknown_outcome" if outcome == "unknown_requires_review" else "confirm_failed"
            screenshot = await self._capture_shot(chat, account.id, label)

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
                "detail": upgrade_detail, "message": message,
                "screenshot": screenshot, "page_url": self._page_url(chat)}

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
        diagnostics: dict | None = None,
    ) -> Path:
        directory = self._settings.artifacts_dir / account_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"send-{_utcnow():%Y%m%d-%H%M%S}.json"
        payload = {
            "account_id": account_id,
            "overall": overall,
            "detail": detail,
            "finished_at": _utcnow().isoformat(),
            "meta": self._diagnostics_meta(),
            "diagnostics": diagnostics,
            "results": results,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ------------------------------------------------------------ shutdown

    async def aclose(self) -> None:
        for progress in list(self._scan_progress.values()):
            if progress.status == "running":
                self._update_scan_progress(
                    progress.task_id,
                    status="interrupted",
                    stage="后端停止，中断扫描",
                    current_item=None,
                    detail="后端在扫描完成前停止，已保留已发现结果；请重新发起扫描",
                    finished_at=_utcnow(),
                )
        for progress in list(self._identity_hydration_progress.values()):
            if progress.status == "running":
                self._update_identity_hydration_progress(
                    progress.task_id,
                    status="interrupted",
                    stage="后端停止，中断身份补采",
                    current_item=None,
                    detail="后端在身份补采完成前停止，已保留已完成回填；请重新发起",
                    finished_at=_utcnow(),
                )
        for flow in list(self._flows.values()):
            if flow.task is not None and not flow.task.done():
                flow.task.cancel()
        for task in list(self._scan_tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._identity_hydration_tasks.values()):
            if not task.done():
                task.cancel()
        for flow in list(self._flows.values()):
            if flow.task is not None:
                try:
                    await flow.task
                except (asyncio.CancelledError, Exception):
                    pass
        for task in list(self._scan_tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for task in list(self._identity_hydration_tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._flows.clear()
        self._scan_tasks.clear()
        self._scan_progress.clear()
        self._identity_hydration_tasks.clear()
        self._identity_hydration_progress.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
