import asyncio
import json
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Awaitable, Callable

# 关键：必须用「模块引用 + 运行期取值」，不能在导入时 `from .selectors import X`
# 捕获常量——那样 selector 热更新对这个文件就失效了。
from . import selectors as _s

logger = logging.getLogger(__name__)

# 抖音输入框发送后会残留零宽字符，普通 strip() 判不清空
_BLANK_RE = re.compile(r"[\s\u200b\u200c\u200d\ufeff]+")
_UNREAD_COUNT_SUFFIX_RE = re.compile(r"\(\d+\)$")

MAX_LIST_SCROLLS = 8
WARMUP_SCROLLS = 5
# 会话页只等 DOM 就绪（不等全部静态资源），慢网络下也留有足够余量
CHAT_PAGE_TIMEOUT_MS = 60_000
SEARCH_RESULTS_TIMEOUT = 12.0
OPEN_CONFIRM_TIMEOUT = 8.0
EDITOR_READY_TIMEOUT = 8.0
# 全量扫描会话列表：滚动轮数上限 + 连续无新增即判定到底
SCAN_MAX_ROUNDS = 80
SCAN_IDLE_ROUNDS = 3
IDENTITY_RESPONSE_PATHS = (
    "/aweme/v1/web/im/user/info",
    "/aweme/v1/web/user/profile/other",
    "/aweme/v1/web/user/profile/scene",
)
# 会话列表的丰富数据由 imapi 以 protobuf 返回；其中每个群会话对象同时带有
# conversation_id、会话类型和展示名称。只监听初始化响应，不保存完整响应体。
CONVERSATION_RESPONSE_PATHS = (
    "/v1/message/get_message_by_init",
)
IDENTITY_LABEL_FIELDS = ("remark_name", "nickname", "unique_id", "short_id")
IDENTITY_ID_FIELDS = ("sec_uid", "sec_user_id", "uid", "user_id")


def _item_selectors() -> tuple:
    """运行期返回「会话列表项 + 搜索结果行」两组选择器（随 bundle 热更）。"""
    return (_s.CONVERSATION_ITEM, _s.SEARCH_RESULT_ITEM)


def _chat_editor_selectors() -> tuple[str, ...]:
    """返回运行期编辑器选择器，首选 bundle 配置并保留兼容回退。"""
    configured = getattr(_s, "CHAT_EDITOR_SELECTORS", (_s.CHAT_EDITOR,))
    if isinstance(configured, str):
        configured = (configured,)
    return tuple(dict.fromkeys((_s.CHAT_EDITOR, *configured)))


class DomChangedError(RuntimeError):
    """关键页面结构不存在，说明抖音页面已改版。"""


class SendOutcomeUnknownError(RuntimeError):
    """已触发不可逆发送动作，但页面未给出足够证据确认结果。"""


def _has_new_media_bubble(
    before: tuple[str, ...] | list[str] | None,
    current: tuple[str, ...] | list[str] | None,
) -> bool:
    """判断消息列表是否出现了发送前不存在的媒体气泡。

    不能只比较总图片数：抖音的虚拟列表可能在插入新消息时回收旧节点，
    导致数量不变。用签名的多重集合比较，既能识别新增消息，也不会把
    「旧节点被回收」误报成成功。
    """
    if before is None or current is None:
        return False
    return bool(Counter(current) - Counter(before))


@dataclass
class ConversationOpen:
    opened: bool
    title: str | None
    exact: bool
    platform_user_id: str | None = None
    identity_verified: bool = False
    platform_conversation_id: str | None = None
    conversation_type: str | None = None


@dataclass(frozen=True)
class IdentityRecord:
    """抖音用户信息响应中可用于展示/核对的一条身份快照。"""

    platform_user_id: str
    platform_handle: str | None = None
    platform_handle_source: str | None = None
    nickname: str | None = None
    remark_name: str | None = None


@dataclass(frozen=True)
class IdentityMatch:
    """会话标题与平台身份的安全匹配结果。"""

    status: str
    platform_user_id: str | None = None
    platform_handle: str | None = None
    platform_nickname: str | None = None
    source: str | None = None
    candidate_count: int = 0
    detail: str | None = None

    def as_entry_fields(self) -> dict[str, object]:
        return {
            "platform_user_id": self.platform_user_id,
            "platform_handle": self.platform_handle,
            "identity_status": self.status,
            "identity_source": self.source,
            "identity_candidate_count": self.candidate_count,
            "identity_detail": self.detail,
        }


@dataclass(frozen=True)
class ConversationRecord:
    """抖音会话初始化响应中的群会话身份。"""

    platform_conversation_id: str
    title: str
    conversation_type: str = "group"


@dataclass(frozen=True)
class ConversationMatch:
    """会话标题与群会话唯一 ID 的安全匹配结果。"""

    status: str
    platform_conversation_id: str | None = None
    conversation_type: str | None = None
    candidate_count: int = 0
    detail: str | None = None

    def as_entry_fields(self) -> dict[str, object]:
        return {
            "platform_conversation_id": self.platform_conversation_id,
            "conversation_type": self.conversation_type,
            "conversation_status": self.status,
            "conversation_candidate_count": self.candidate_count,
            "conversation_detail": self.detail,
        }


def _read_proto_varint(data: bytes, offset: int) -> tuple[int, int] | None:
    """读取一个 protobuf varint；畸形数据返回 None，扫描按未知处理。"""
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7
    return None


def _decode_proto_fields(data: bytes) -> list[tuple[int, int, int | bytes]] | None:
    """解码当前 protobuf 消息的一层字段，不依赖抖音私有生成代码。"""
    fields: list[tuple[int, int, int | bytes]] = []
    offset = 0
    while offset < len(data):
        key_result = _read_proto_varint(data, offset)
        if key_result is None:
            return None
        key, offset = key_result
        field_number, wire_type = key >> 3, key & 0x07
        if field_number <= 0:
            return None
        if wire_type == 0:
            value_result = _read_proto_varint(data, offset)
            if value_result is None:
                return None
            value, offset = value_result
        elif wire_type == 1:
            if offset + 8 > len(data):
                return None
            value = data[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            length_result = _read_proto_varint(data, offset)
            if length_result is None:
                return None
            length, offset = length_result
            if length > len(data) - offset:
                return None
            value = data[offset : offset + length]
            offset += length
        elif wire_type == 5:
            if offset + 4 > len(data):
                return None
            value = data[offset : offset + 4]
            offset += 4
        else:
            # 当前响应没有使用 group wire type；遇到未知结构宁可不绑定。
            return None
        fields.append((field_number, wire_type, value))
    return fields


def _iter_proto_messages(data: bytes, depth: int = 0):
    """遍历可完整解码的嵌套消息；只用于提取已知形状，失败即跳过。"""
    fields = _decode_proto_fields(data)
    if fields is None:
        return
    yield fields
    if depth >= 10:
        return
    for _field_number, wire_type, value in fields:
        if wire_type == 2 and isinstance(value, bytes) and 1 < len(value) <= 4_000_000:
            yield from _iter_proto_messages(value, depth + 1)


def _proto_text(value: int | bytes) -> str | None:
    if not isinstance(value, bytes):
        return None
    try:
        text = value.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text or len(text) > 80 or any(ord(char) < 32 for char in text):
        return None
    return text


def parse_group_conversations(data: bytes) -> list[ConversationRecord]:
    """从 get_message_by_init protobuf 中提取群会话 ID/名称。

    当前响应的群会话对象具有稳定形状：field 1 是数字字符串 ID，field 2
    是同一 ID 的 varint，field 3 为 2（群会话），field 5 为展示名称。
    同时校验 field 1/2，避免把消息中的普通数字或用户 ID 猜成会话 ID。
    """
    records: list[ConversationRecord] = []
    seen: set[tuple[str, str]] = set()
    for fields in _iter_proto_messages(data):
        field_1 = [value for number, wire, value in fields if number == 1 and wire == 2]
        field_2 = [value for number, wire, value in fields if number == 2 and wire == 0]
        field_3 = [value for number, wire, value in fields if number == 3 and wire == 0]
        field_5 = [value for number, wire, value in fields if number == 5 and wire == 2]
        conversation_id = next(
            (
                value.decode("ascii")
                for value in field_1
                if isinstance(value, bytes)
                and 15 <= len(value) <= 25
                and value.isdigit()
            ),
            None,
        )
        if conversation_id is None or int(conversation_id) not in field_2 or 2 not in field_3:
            continue
        title = next((text for value in field_5 if (text := _proto_text(value))), None)
        if title is None:
            continue
        key = conversation_id, title
        if key in seen:
            continue
        seen.add(key)
        records.append(
            ConversationRecord(
                platform_conversation_id=conversation_id,
                title=title,
            )
        )
    return records


@dataclass
class Sticker:
    """抖音原生表情（sticker）配置（参考 unmev/douyin-auto-fire）。

    name 为表情名（value）；label 为面板显示名（默认=name，用于精确匹配）；
    category 为表情分类（可选，打开面板先点该分类）；fallback_index 为兜底索引。
    """

    name: str
    label: str | None = None
    category: str | None = None
    fallback_index: int | None = None


class DouyinChat:
    """对单个 Playwright page 的窄封装；所有页面交互集中在这里。

    page 只需实现 goto/query_selector/query_selector_all/fill/click/inner_text/
    type_text/press_key，便于在测试中注入假实现，也便于未来替换执行后端。
    """

    def __init__(
        self,
        page,
        timeout_ms: int = 15_000,
        confirm_timeout: float = OPEN_CONFIRM_TIMEOUT,
        search_timeout: float = SEARCH_RESULTS_TIMEOUT,
        sleep=asyncio.sleep,
    ):
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirm_timeout = confirm_timeout
        self.search_timeout = search_timeout
        self._sleep = sleep
        self._warmed = False
        # 抖音会话列表 DOM 当前不暴露用户 ID；页面加载/打开会话时的用户信息
        # 响应会带 sec_uid。一个显示名可能对应多人，因此保留集合，冲突时返回未知。
        self._identity_ids_by_label: dict[str, set[str]] = {}
        self._identity_records_by_id: dict[str, IdentityRecord] = {}
        self._identity_tasks: set[asyncio.Task] = set()
        # 群聊的唯一会话 ID 在 imapi protobuf 中；同名群聊也必须保留集合，
        # 不能为了方便把一个 ID 猜给另一个同名会话。
        self._conversation_ids_by_label: dict[str, set[str]] = {}
        self._conversation_records_by_id: dict[str, ConversationRecord] = {}
        self._conversation_tasks: set[asyncio.Task] = set()
        on_response = getattr(page, "on", None)
        if callable(on_response):
            try:
                on_response("response", self._on_response)
            except Exception:
                # 测试桩或替代页面协议可以没有事件总线；此时仍保留昵称严格匹配。
                pass

    def _on_response(self, response) -> None:
        """异步接收身份响应；只提取必要字段，不把完整响应保存到本地。"""
        url = getattr(response, "url", None)
        if not isinstance(url, str):
            return
        is_identity_response = any(path in url for path in IDENTITY_RESPONSE_PATHS)
        is_conversation_response = any(path in url for path in CONVERSATION_RESPONSE_PATHS)
        if not is_identity_response and not is_conversation_response:
            return
        try:
            capture = (
                self._capture_identity_response
                if is_identity_response
                else self._capture_conversation_response
            )
            task = asyncio.create_task(capture(response))
        except RuntimeError:
            return
        tasks = self._identity_tasks if is_identity_response else self._conversation_tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def _capture_identity_response(self, response) -> None:
        try:
            payload = await response.json()
        except Exception:
            return
        for record in self._iter_identity_records(payload):
            platform_user_id = self._first_non_empty(
                self._field_value(record, "sec_uid", "sec_user_id", "secUid", "secUserId"),
                self._field_value(record, "uid", "user_id", "userId"),
            )
            if platform_user_id is None:
                continue
            identity = IdentityRecord(
                platform_user_id=platform_user_id,
                platform_handle=(
                    self._first_non_empty(self._field_value(record, "unique_id", "uniqueId"))
                    or self._first_non_empty(self._field_value(record, "short_id", "shortId"))
                ),
                platform_handle_source=(
                    "unique_id"
                    if self._first_non_empty(self._field_value(record, "unique_id", "uniqueId"))
                    else "short_id"
                    if self._first_non_empty(self._field_value(record, "short_id", "shortId"))
                    else None
                ),
                nickname=self._first_non_empty(
                    self._field_value(record, "nickname", "nickName"),
                ),
                remark_name=self._first_non_empty(
                    self._field_value(record, "remark_name", "remarkName"),
                ),
            )
            previous = self._identity_records_by_id.get(platform_user_id)
            if previous is not None:
                identity = IdentityRecord(
                    platform_user_id=platform_user_id,
                    platform_handle=identity.platform_handle or previous.platform_handle,
                    platform_handle_source=identity.platform_handle_source or previous.platform_handle_source,
                    nickname=identity.nickname or previous.nickname,
                    remark_name=identity.remark_name or previous.remark_name,
                )
            self._identity_records_by_id[platform_user_id] = identity
            for field in IDENTITY_LABEL_FIELDS:
                label = self._first_non_empty(
                    self._field_value(record, field, self._camelize_identity_field(field)),
                )
                if label is not None:
                    key = self._normalize_identity_label(label)
                    self._identity_ids_by_label.setdefault(key, set()).add(platform_user_id)

    async def _capture_conversation_response(self, response) -> None:
        try:
            body = await response.body()
        except Exception:
            return
        if not isinstance(body, bytes):
            return
        for record in parse_group_conversations(body):
            previous = self._conversation_records_by_id.get(record.platform_conversation_id)
            if previous is not None and previous.title == record.title:
                continue
            self._conversation_records_by_id[record.platform_conversation_id] = record
            key = self._normalize_identity_label(record.title)
            self._conversation_ids_by_label.setdefault(key, set()).add(
                record.platform_conversation_id
            )

    @classmethod
    def _iter_identity_records(cls, value):
        if isinstance(value, dict):
            if any(
                cls._field_value(value, field, cls._camelize_identity_field(field))
                for field in (*IDENTITY_LABEL_FIELDS, *IDENTITY_ID_FIELDS)
            ):
                yield value
            for child in value.values():
                yield from cls._iter_identity_records(child)
        elif isinstance(value, list):
            for child in value:
                yield from cls._iter_identity_records(child)

    @staticmethod
    def _first_non_empty(*values) -> str | None:
        for value in values:
            if isinstance(value, (str, int)):
                text = str(value).strip()
                if text:
                    return text
        return None

    @staticmethod
    def _camelize_identity_field(field: str) -> str:
        head, *tail = field.split("_")
        return head + "".join(part.capitalize() for part in tail)

    @classmethod
    def _field_value(cls, value: dict, *fields: str):
        for field in fields:
            if field in value:
                return value.get(field)
        return None

    @staticmethod
    def _normalize_identity_label(label: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", label)).strip()

    def _identity_ids_for_label(self, label: str | None) -> set[str]:
        if not label:
            return set()
        return self._identity_ids_by_label.get(self._normalize_identity_label(label), set())

    def _identity_match_for_ids(self, normalized: str, ids: set[str]) -> IdentityMatch:
        """按一组本次观察到的候选 ID 生成安全匹配结果。"""
        if not ids:
            return IdentityMatch(
                status="not_observed",
                detail="本次扫描未观察到带此名称的用户身份数据，未写入稳定身份",
            )
        if len(ids) > 1:
            return IdentityMatch(
                status="ambiguous",
                candidate_count=len(ids),
                detail="同名对应多个平台用户，已阻止自动绑定",
            )

        platform_user_id = next(iter(ids))
        record = self._identity_records_by_id.get(platform_user_id)
        source = None
        if record is not None:
            for field, value in (
                ("remark_name", record.remark_name),
                ("nickname", record.nickname),
                (record.platform_handle_source, record.platform_handle),
            ):
                if value and self._normalize_identity_label(value) == normalized:
                    source = field
                    break
        return IdentityMatch(
            status="bound",
            platform_user_id=platform_user_id,
            platform_handle=record.platform_handle if record else None,
            platform_nickname=record.nickname if record else None,
            source=source,
            candidate_count=1,
            detail=(f"通过{self._identity_source_label(source)}唯一匹配" if source else "已唯一匹配平台身份"),
        )

    def identity_match_for_label(self, label: str | None) -> IdentityMatch:
        """按显示名返回身份匹配诊断；无法唯一确认时明确保持未绑定。"""
        normalized = self._normalize_identity_label(label) if label else ""
        return self._identity_match_for_ids(normalized, self._identity_ids_for_label(normalized))

    async def hydrate_identity_for_label(self, label: str) -> tuple[ConversationOpen, IdentityMatch]:
        """打开一个精确会话并只使用本次打开后新到达的身份响应。"""
        normalized = self._normalize_identity_label(label)
        before = set(self._identity_ids_for_label(normalized))
        opened = await self.open_conversation(label)
        # Playwright 通常会在点击后同步派发 response 事件；给替代页面协议一个
        # 调度机会，确保事件处理任务已登记后再等待它完成。
        await self._sleep(0)
        await self._wait_for_identity_updates()
        if not opened.opened or not opened.exact:
            return opened, IdentityMatch(
                status="not_observed",
                detail="未能精确打开该好友会话，未写入稳定身份",
            )
        fresh = self._identity_ids_for_label(normalized) - before
        if not fresh:
            return opened, IdentityMatch(
                status="not_observed",
                detail="已精确打开会话，但未观察到本次用户身份响应，未写入稳定身份",
            )
        return opened, self._identity_match_for_ids(normalized, fresh)

    @staticmethod
    def _identity_source_label(source: str | None) -> str:
        return {
            "remark_name": "备注名",
            "nickname": "平台昵称",
            "unique_id": "抖音号",
            "short_id": "抖音号",
        }.get(source or "", "平台身份")

    def platform_user_id_for_label(self, label: str | None) -> str | None:
        """返回唯一可确认的用户 ID；同名冲突或未采集到时返回 None。"""
        match = self.identity_match_for_label(label)
        return match.platform_user_id if match.status == "bound" else None

    def _conversation_ids_for_label(self, label: str | None) -> set[str]:
        if not label:
            return set()
        return self._conversation_ids_by_label.get(self._normalize_identity_label(label), set())

    def conversation_match_for_label(self, label: str | None) -> ConversationMatch:
        """按会话展示名返回群聊唯一 ID；同名群聊保持未绑定。"""
        normalized = self._normalize_identity_label(label) if label else ""
        ids = self._conversation_ids_for_label(normalized)
        if not ids:
            return ConversationMatch(
                status="not_observed",
                detail="本次扫描未观察到带唯一 ID 的群会话数据，未猜测绑定",
            )
        if len(ids) > 1:
            return ConversationMatch(
                status="ambiguous",
                candidate_count=len(ids),
                detail="同名对应多个群会话，已阻止自动绑定",
            )
        platform_conversation_id = next(iter(ids))
        record = self._conversation_records_by_id.get(platform_conversation_id)
        return ConversationMatch(
            status="bound",
            platform_conversation_id=platform_conversation_id,
            conversation_type=record.conversation_type if record else "group",
            candidate_count=1,
            detail="通过抖音会话 protobuf 唯一匹配群聊",
        )

    def platform_conversation_id_for_label(self, label: str | None) -> str | None:
        """返回唯一可确认的群会话 ID；同名冲突或未采集到时返回 None。"""
        match = self.conversation_match_for_label(label)
        return (
            match.platform_conversation_id
            if match.status == "bound"
            else None
        )

    async def _wait_for_identity_updates(self) -> None:
        while self._identity_tasks or self._conversation_tasks:
            tasks = tuple(self._identity_tasks | self._conversation_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)

    def _opened_identity_matches(
        self,
        requested_name: str,
        right_title: str | None,
        expected_platform_user_id: str,
    ) -> tuple[bool, str | None]:
        primary = self._primary_panel_title(right_title)
        # 标题仍相同，优先按请求名核对；标题已改名时，按当前右侧主昵称核对。
        label = requested_name if primary == requested_name else primary
        ids = self._identity_ids_for_label(label)
        if ids == {expected_platform_user_id}:
            return True, expected_platform_user_id
        if len(ids) == 1:
            return False, next(iter(ids))
        return False, None

    def _opened_conversation_matches(
        self,
        requested_name: str,
        right_title: str | None,
        expected_platform_conversation_id: str,
    ) -> tuple[bool, str | None]:
        primary = self._primary_panel_title(right_title)
        label = requested_name if primary == requested_name else primary
        ids = self._conversation_ids_for_label(label)
        if ids == {expected_platform_conversation_id}:
            return True, expected_platform_conversation_id
        if len(ids) == 1:
            return False, next(iter(ids))
        return False, None

    async def open_chat_page(self) -> None:
        """打开会话页。

        douyin 页面会持续加载大量静态资源；默认的 `wait_until="load"` 在网络到
        国内 CDN 较慢的机器上（例如云服务器）会超过 30s 超时，把整个流程误判为
        失败。这里只等 DOM 就绪，页面是否可用一律交给后续元素轮询判断。
        """
        await self.page.goto(
            _s.CHAT_URL, wait_until="domcontentloaded", timeout=CHAT_PAGE_TIMEOUT_MS
        )
        await self._sleep(3)

    async def is_logged_in(self) -> bool:
        element = await self.page.query_selector(_s.CONVERSATION_LIST)
        return element is not None

    async def is_verification_required(self) -> bool:
        """检测扫码后是否出现「身份验证」风控弹窗（新设备/异地登录常见）。

        出现时流程不应按普通超时收场：部分验证方式（如授权历史记录验证）
        由账号本人在抖音 App 内确认，只要继续等就能自动完成登录。
        保守策略：页面里找不到该文案或查询异常，一律按「未出现」处理，
        回到原本的超时路径——检测不到只影响体验，不影响正确性。
        """
        try:
            return await self.page.locator("text=身份验证").count() > 0
        except Exception:  # noqa: BLE001 - 检测是尽力而为，异常按未出现处理
            return False

    async def wait_logged_in(
        self,
        timeout: float,
        poll_interval: float = 0.5,
    ) -> bool:
        """轮询等待会话列表出现（headless/登录后列表可能延迟渲染）。

        背景：`is_logged_in()` 是单次查询——页面刚 goto 完、会话列表异步渲染，
        立刻查询可能返回 False，被当作「未登录」误标 auth_expired 并跳过发送。
        本方法在 timeout 内反复轮询会话列表：一旦出现即判已登录；超时仍未出现
        才判未登录（未登录时最多等 timeout，避免无谓阻塞）。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        transient_logged = False
        while True:
            try:
                if await self.is_logged_in():
                    return True
            except Exception:  # noqa: BLE001 - 轮询判据，瞬时错误按「还没登录」重试
                # 扫码被确认后页面会跳转/重载，轮询期间可能撞上「执行上下文已销毁」
                # 这类瞬时错误；它是轮询判据，不该因一次瞬时错误把整个流程判死。
                if not transient_logged:
                    transient_logged = True
                    logger.warning("会话列表轮询遇到瞬时错误，继续重试", exc_info=True)
            if loop.time() >= deadline:
                return False
            await self._sleep(poll_interval)

    async def right_panel_title(self) -> str | None:
        element = await self.page.query_selector(_s.RIGHT_TITLE)
        if element is None:
            return None
        text = await element.inner_text()
        return text.strip() if text else None

    async def search(self, name: str) -> None:
        await self._fill_first(_s.SEARCH_INPUTS, name)
        await self._sleep(2.2)

    async def _fill_first(self, selectors: tuple[str, ...], value: str) -> None:
        for selector in selectors:
            if await self.page.query_selector(selector) is not None:
                await self.page.fill(selector, value)
                return

    async def warm_up_list(self) -> None:
        """滚动列表触发懒加载：会话列表与搜索索引都是分页拉取的。

        注意必须传入 ElementHandle——传选择器字符串时 JS 对字符串设置
        scrollTop 会静默无效，预热就成了空转。
        """
        if self._warmed:
            return
        for _ in range(WARMUP_SCROLLS):
            element = await self.page.query_selector(_s.CONVERSATION_LIST)
            if element is None:
                break
            try:
                await self.page.evaluate("(el) => { el.scrollTop += 900 }", element)
            except Exception:
                break
            await self._sleep(0.9)
        self._warmed = True

    async def open_conversation(
        self,
        name: str,
        platform_user_id: str | None = None,
        platform_conversation_id: str | None = None,
    ) -> ConversationOpen:
        # 0) 预热：先滚动列表，让懒加载数据与搜索索引就绪
        try:
            await self.warm_up_list()
        except Exception:
            pass
        # 1) 搜索优先：直接搜索昵称，从结果中精确匹配
        try:
            await self.search(name)
            result = await self._search_and_open(
                name,
                platform_user_id,
                platform_conversation_id,
            )
            if result is not None:
                return result
        except Exception:
            pass
        # 2) 清空搜索，滚动加载列表兜底（会话列表可能是懒加载）
        try:
            await self._fill_first(_s.SEARCH_INPUTS, "")
            await self._sleep(0.8)
        except Exception:
            pass
        for _ in range(MAX_LIST_SCROLLS):
            result = await self._scan_items(
                name,
                platform_user_id,
                platform_conversation_id,
            )
            if result.opened:
                return result
            scroll_element = await self.page.query_selector(_s.CONVERSATION_LIST)
            if scroll_element is None:
                break
            await self.page.evaluate("(el) => { el.scrollTop += 900 }", scroll_element)
            await self._sleep(0.8)
        return ConversationOpen(opened=False, title=None, exact=False)

    async def _try_click_exact_by_text(self, name: str) -> bool:
        """不依赖类名的兜底：找文本精确等于昵称的行，点击其「发消息」按钮。

        只在真实 Playwright page 上生效；身份安全仍由点击后的右侧标题确认兜底。
        """
        page = self.page
        if not hasattr(page, "evaluate_handle"):
            return False
        handle = await page.evaluate_handle(
            """(name) => {
              const all = Array.from(document.querySelectorAll('div'));
              for (const el of all) {
                const txt = (el.textContent || '').trim();
                if (txt !== name && !txt.startsWith(name + '\\n')) continue;
                const row = el.closest('[class*="Search"], [class*="search"]') || el;
                const btn = Array.from(row.querySelectorAll('*')).find(
                  (n) => (n.textContent || '').trim() === '发消息'
                );
                return btn || row;
              }
              return null;
            }""",
            name,
        )
        if handle is None:
            return False
        element = handle.as_element()
        if element is None:
            return False
        try:
            await element.click(timeout=5000)
            await self._sleep(1.2)
            return True
        except Exception:
            return False

    async def collect_search_diagnostics(self, name: str) -> dict:
        """在搜索状态下采集真实 DOM 诊断（类名、输入框属性、文本命中元素）。"""
        page = self.page
        try:
            await self.search(name)
        except Exception:
            pass
        script = """(name) => {
          const classes = new Set();
          document.querySelectorAll('[class*="Search"], [class*="search"]').forEach((el) => {
            if (typeof el.className === 'string' && el.className) classes.add(el.className);
          });
          const textHits = [];
          document.querySelectorAll('div, span, button').forEach((el) => {
            if (textHits.length >= 20) return;
            if ((el.textContent || '').trim() === name) {
              textHits.push({ tag: el.tagName, cls: typeof el.className === 'string' ? el.className : '' });
            }
          });
          const inputs = Array.from(document.querySelectorAll('input[placeholder]')).map((i) => ({
            placeholder: i.placeholder, cls: typeof i.className === 'string' ? i.className : '',
          }));
          return { inputs, searchClasses: Array.from(classes).slice(0, 60), textHits };
        }"""
        try:
            if hasattr(page, "evaluate_js"):
                result = await page.evaluate_js(script(name))
            elif hasattr(page, "evaluate"):
                result = await page.evaluate(script, name)
            else:
                result = {}
        except Exception:
            result = {}
        try:
            await self._fill_first(_s.SEARCH_INPUTS, "")
        except Exception:
            pass
        return result

    async def _search_and_open(
        self,
        name: str,
        platform_user_id: str | None = None,
        platform_conversation_id: str | None = None,
    ) -> ConversationOpen | None:
        """搜索后轮询等待结果渲染（首次加载时结果可能晚于固定等待时间）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.search_timeout
        while True:
            result = await self._scan_items(
                name,
                platform_user_id,
                platform_conversation_id,
            )
            if result.opened:
                return result
            if loop.time() >= deadline:
                break
            await self._sleep(1.0)
        if await self._try_click_exact_by_text(name):
            return await self._confirm_opened(
                name,
                platform_user_id,
                platform_conversation_id,
            )
        return None

    async def _scan_items(
        self,
        name: str,
        platform_user_id: str | None = None,
        platform_conversation_id: str | None = None,
    ) -> ConversationOpen:
        identity_mismatch: ConversationOpen | None = None
        for selector in _item_selectors():
            items = await self.page.query_selector_all(selector)
            for item in items:
                title = await self._title_of(item)
                if title != name:
                    continue
                target = await item.query_selector(_s.SEARCH_CHAT_BTN)
                if target is None:
                    target = item
                try:
                    await target.click(timeout=2500)
                except Exception:
                    continue  # 隐藏缓存行等不可点目标，跳过继续扫描
                opened = await self._confirm_opened(
                    name,
                    platform_user_id,
                    platform_conversation_id,
                )
                if (
                    platform_user_id is None
                    and platform_conversation_id is None
                ) or opened.identity_verified:
                    return opened
                # 同名结果可能不止一个：继续核验其它候选，最终仍以稳定 ID 为准。
                identity_mismatch = opened
        return identity_mismatch or ConversationOpen(opened=False, title=None, exact=False)

    async def _confirm_opened(
        self,
        name: str,
        platform_user_id: str | None = None,
        platform_conversation_id: str | None = None,
    ) -> ConversationOpen:
        """点击后轮询右侧标题确认（SPA 切换有延迟，同 unmev 的 _confirm_opened）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.confirm_timeout
        right_title: str | None = None
        while True:
            right_title = await self.right_panel_title()
            primary = self._primary_panel_title(right_title)
            if platform_user_id is not None:
                await self._wait_for_identity_updates()
                matched, observed = self._opened_identity_matches(
                    name,
                    right_title,
                    platform_user_id,
                )
                if matched:
                    return ConversationOpen(
                        opened=True,
                        title=right_title,
                        exact=True,
                        platform_user_id=observed,
                        identity_verified=True,
                    )
                if observed is not None:
                    return ConversationOpen(
                        opened=True,
                        title=right_title,
                        exact=False,
                        platform_user_id=observed,
                    )
            elif platform_conversation_id is not None:
                await self._wait_for_identity_updates()
                matched, observed = self._opened_conversation_matches(
                    name,
                    right_title,
                    platform_conversation_id,
                )
                if matched:
                    return ConversationOpen(
                        opened=True,
                        title=right_title,
                        exact=True,
                        platform_conversation_id=observed,
                        conversation_type="group",
                        identity_verified=True,
                    )
                if observed is not None:
                    return ConversationOpen(
                        opened=True,
                        title=right_title,
                        exact=False,
                        platform_conversation_id=observed,
                        conversation_type="group",
                    )
            elif primary == name:
                return ConversationOpen(opened=True, title=right_title, exact=True)
            if loop.time() >= deadline:
                return ConversationOpen(
                    opened=True,
                    title=right_title,
                    exact=False,
                    platform_user_id=self.platform_user_id_for_label(primary),
                    platform_conversation_id=self.platform_conversation_id_for_label(primary),
                    conversation_type=(
                        "group"
                        if self.platform_conversation_id_for_label(primary)
                        else None
                    ),
                )
            await self._sleep(0.5)

    @staticmethod
    def _primary_panel_title(value: str | None) -> str | None:
        """取右侧标题中的主昵称，忽略官方火花徽标占用的后续行。

        抖音会把未读数渲染成「名称(9)」，再把「正常天数」「重燃中 X/3」
        等火花状态追加到右侧标题文本中。这里只移除明确的数字未读后缀，
        主昵称仍必须是首个非空行的严格匹配，不能退化为模糊包含。
        """
        if not value:
            return None
        for line in value.splitlines():
            primary = line.strip()
            if primary:
                return _UNREAD_COUNT_SUFFIX_RE.sub("", primary).strip()
        return None

    async def _title_of(self, item) -> str | None:
        """按 unmev 的顺序取标题：搜索行名称节点 → 会话标题子元素 → 整行首行兜底。"""
        # 搜索结果的完整 title 节点还包含「(未读数)」后缀；优先取抖音实际
        # 高亮名称节点，避免「老龙帮\n(9)」无法与请求名严格相等。
        for selector in (*_s.SEARCH_NAME_EXACT_SELECTORS, *_s.SEARCH_NAME_SELECTORS):
            element = await item.query_selector(selector)
            if element is not None:
                text = await element.inner_text()
                if text and text.strip():
                    return self._first_non_empty_line(text)
        title_element = await item.query_selector(_s.CONVERSATION_TITLE)
        if title_element is not None:
            text = await title_element.inner_text()
            if text and text.strip():
                return self._first_non_empty_line(text)
        text = await item.inner_text()
        if not text:
            return None
        return self._first_non_empty_line(text)

    @staticmethod
    def _first_non_empty_line(text: str) -> str | None:
        for line in text.splitlines():
            value = line.strip()
            if value:
                return value
        return None

    async def visible_titles(self, limit: int = 40) -> list[str]:
        titles: list[str] = []
        seen: set[str] = set()
        for selector in _item_selectors():
            items = await self.page.query_selector_all(selector)
            for item in items:
                title_element = await item.query_selector(_s.CONVERSATION_TITLE)
                if title_element is None:
                    continue
                title = self._first_non_empty_line((await title_element.inner_text()) or "")
                if title and title not in seen:
                    seen.add(title)
                    titles.append(title)
                    if len(titles) >= limit:
                        return titles
        return titles

    async def scan_all_chats(
        self,
        progress_callback: Callable[[int, int, str | None, list[dict]], Awaitable[None]] | None = None,
    ) -> list[dict]:
        """全量扫描会话列表：边滚动边收集去重，连续多轮无新增即判定到底。

        只读操作——不点击任何会话、不发送任何消息；供好友批量导入挑选候选。
        返回结构化条目：title=会话名，preview=最后一条消息，
        streak_days=抖音官方火花天数（无火花标记为 None——这就是续火对象的判定信号），
        streak_kind=火花类型（normal 普通 / couple 情侣 / family 家人，取自图标路径）。

        progress_callback 只报告已发现数量、当前滚动轮次、最近发现的标题和结果快照。
        全量列表在滚动到底前无法知道最终总数，因此调用方不应把轮次上限当作百分比。
        """
        entries: list[dict] = []
        seen: set[str] = set()
        scroll_element = await self.page.query_selector(_s.CONVERSATION_LIST)
        if scroll_element is None:
            return entries
        idle = 0
        last_round = 0
        for round_no in range(1, SCAN_MAX_ROUNDS + 1):
            last_round = round_no
            count_before = len(entries)
            for item in await self.page.query_selector_all(_s.CONVERSATION_ITEM):
                title_element = await item.query_selector(_s.CONVERSATION_TITLE)
                if title_element is None:
                    continue
                title = self._first_non_empty_line(
                    (await title_element.inner_text()) or ""
                )
                if not title or title in seen:
                    continue
                seen.add(title)

                streak_days: int | None = None
                streak_kind: str | None = None
                streak_label: str | None = None
                streak_el = await item.query_selector(_s.CONVERSATION_STREAK)
                if streak_el is not None:
                    days_el = await streak_el.query_selector(_s.CONVERSATION_STREAK_DAYS)
                    days_text = ((await days_el.inner_text()) or "").strip() if days_el else ""
                    if days_text.isdigit():
                        # 正常态：纯数字 = 火花天数
                        streak_days = int(days_text)
                    elif days_text:
                        # 特殊态：「重燃中 1/3」「N 天后消失」等文字标签
                        streak_label = days_text
                    icon = await streak_el.query_selector("img[src*='flame_icon']")
                    src = (await icon.get_attribute("src")) if icon else None
                    if src:
                        # 图标路径形如 /flame_icon/<kind>/<variant>.png
                        for kind in ("couple", "family", "normal"):
                            if f"/{kind}/" in src:
                                streak_kind = kind
                                break

                lines = [line.strip() for line in ((await item.inner_text()) or "").splitlines() if line.strip()]
                # 行结构：[名字, (火花标记), 时间, 最后消息…]；滤掉名字与火花标记行后取消息
                marks = {str(streak_days or ""), streak_label or ""}
                rest = [line for line in lines[1:] if line not in marks]
                preview = " ".join(rest[1:]) if len(rest) > 1 else ""
                entries.append(
                    {
                        "title": title,
                        "preview": preview,
                        "streak_days": streak_days,
                        "streak_kind": streak_kind,
                        "streak_label": streak_label,
                    }
                )
                if progress_callback is not None:
                    await progress_callback(len(entries), round_no, title, list(entries))
            idle = idle + 1 if len(entries) == count_before else 0
            at_bottom = await self.page.evaluate(
                "(el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 5",
                scroll_element,
            )
            if at_bottom or idle >= SCAN_IDLE_ROUNDS:
                break
            await self.page.evaluate("(el) => { el.scrollTop += 900 }", scroll_element)
            await self._sleep(0.8)
        await self._wait_for_identity_updates()
        for entry in entries:
            entry.update(self.identity_match_for_label(entry["title"]).as_entry_fields())
            entry.update(self.conversation_match_for_label(entry["title"]).as_entry_fields())
        if progress_callback is not None:
            await progress_callback(len(entries), last_round, None, list(entries))
        return entries

    async def _is_visible(self, element) -> bool:
        checker = getattr(element, "is_visible", None)
        if not callable(checker):
            return True
        try:
            return await checker()
        except Exception:
            return True

    async def _wait_for_chat_editor(
        self,
        timeout: float = EDITOR_READY_TIMEOUT,
    ) -> tuple[str | None, object | None]:
        """等待当前会话的可见编辑器完成异步渲染并返回命中的选择器。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 0.0)
        while True:
            for selector in _chat_editor_selectors():
                elements = []
                try:
                    elements = await self.page.query_selector_all(selector)
                except Exception:
                    pass
                if not elements:
                    try:
                        element = await self.page.query_selector(selector)
                    except Exception:
                        element = None
                    if element is not None:
                        elements = [element]
                for element in elements:
                    if await self._is_visible(element):
                        return selector, element
            if loop.time() >= deadline:
                return None, None
            await self._sleep(0.2)

    async def _type_text(self, text: str, selector: str | None = None) -> None:
        """输入文本：真实页面走 IME 安全的 insert_text；测试协议方法兜底。"""
        page = self.page
        if hasattr(page, "type_text"):
            await page.type_text(selector or _s.CHAT_EDITOR, text)
        elif hasattr(page, "keyboard"):
            await page.keyboard.insert_text(text)
        else:
            raise DomChangedError("页面对象不支持文本输入")

    async def _press_key(self, key: str) -> None:
        page = self.page
        if hasattr(page, "press_key"):
            await page.press_key(key)
        elif hasattr(page, "keyboard"):
            await page.keyboard.press(key)
        else:
            raise DomChangedError("页面对象不支持按键")

    async def _text_in_editor(self, content: str) -> bool:
        """确认文字确实写入了编辑器（新版 Draft.js 对程序化输入可能不触发内部状态）。"""
        page = self.page
        if hasattr(page, "wait_for_function"):
            try:
                await page.wait_for_function(
                    """({expected, selectors}) => {
                        const editors = selectors.flatMap((selector) => [
                          ...document.querySelectorAll(selector),
                        ]);
                        return editors.some((e) => (e.innerText || '').includes(expected));
                    }""",
                    arg={"expected": content, "selectors": list(_chat_editor_selectors())},
                    timeout=5000,
                )
                return True
            except Exception:
                return False
        _selector, editor = await self._wait_for_chat_editor(timeout=0)
        if editor is None:
            return False
        text = await editor.inner_text()
        return content in (text or "")

    async def _trigger_send(self) -> None:
        """新版编辑器按 Enter 不发送：优先点击发布按钮，Enter 仅兜底（同 unmev）。"""
        page = self.page
        if hasattr(page, "locator"):
            for selector in _s.SEND_BUTTONS:
                locator = page.locator(selector).first
                try:
                    if await locator.count() and await locator.is_visible():
                        await locator.click()
                        return
                except Exception:
                    continue
        await self._press_key("Enter")

    async def send_text(self, text: str) -> None:
        self.last_bubble_seen = False
        editor_selector, editor = await self._wait_for_chat_editor()
        if editor is None:
            raise DomChangedError("未找到消息输入框")
        await editor.click()
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line:
                await self._type_text(line, editor_selector)
            if index < len(lines) - 1:
                await self._press_key("Shift+Enter")

        # 程序化输入必须验证文字确实进入编辑器
        if not await self._text_in_editor(text.replace("\n", "")):
            raise DomChangedError("文字未能写入聊天输入框")
        await self._trigger_send()

        # 权威成功信号：消息列表出现包含本条渲染文本的 outgoing 气泡。
        # 仅真实 Playwright 页面具备 wait_for_function；测试假页面走不到该
        # 路径，由 manager 以编辑器清空路径兜底判定。
        content_key = _BLANK_RE.sub("", text)
        if hasattr(self.page, "wait_for_function"):
            try:
                await self.page.wait_for_function(
                    """(expected) => {
                        const norm = (v) => (v || '').replace(/[\\s\\u200B\\u200C\\u200D\\uFEFF]+/g, ' ').trim();
                        const expectedNorm = norm(expected);
                        const boxes = document.querySelectorAll('.messageMessageBoxcontentBox');
                        for (const box of boxes) {
                            if (norm(box.innerText).includes(expectedNorm)) return true;
                        }
                        return false;
                    }""",
                    arg=content_key,
                    timeout=10_000,
                )
                self.last_bubble_seen = True
            except Exception:
                self.last_bubble_seen = False

    async def editor_is_cleared(self) -> bool:
        _selector, element = await self._wait_for_chat_editor(timeout=0)
        if element is None:
            return True
        text = await element.inner_text()
        return not (_BLANK_RE.sub("", text or ""))

    async def failure_marker_visible(self) -> bool:
        for selector in _s.SEND_FAILURE_MARKERS:
            if await self.page.query_selector(selector) is not None:
                return True
        return False

    # ------------------------------------------------------------ 图片发送

    async def send_image(self, image_path: str) -> None:
        """发送一张本地图片（当前抖音聊天版流程：点附件图标 → 选图 → 确认弹窗发）。

        步骤：
        1. 点 `.semi-upload-add`（附件图标）唤起文件选择器；
        2. 通过 filechooser 注入本地图片；
        3. 等待上传并弹出「发送给 xxx」确认弹窗，点弹窗内的「发送」；
        4. 等待图片气泡真实出现（排除头像，防误判）。

        找不到附件图标时抛 DomChangedError（进入可重试而非半成功）。
        last_bubble_seen 记录"本次发出的图片气泡已出现"，供 _confirm_send 判定。
        """
        self.last_bubble_seen = False
        self._image_before_count = await self._count_non_avatar_images()
        self._media_before_snapshot = await self._media_bubble_snapshot()
        if not hasattr(self.page, "expect_file_chooser"):
            raise DomChangedError("页面对象不支持文件选择器")
        try:
            async with self.page.expect_file_chooser(timeout=8000) as fc_info:
                await self._click_upload_trigger()
            file_chooser = await fc_info.value
        except DomChangedError:
            raise
        except Exception as exc:
            raise DomChangedError(f"未找到图片上传入口：{type(exc).__name__}") from None

        await file_chooser.set_files(image_path)
        await self._sleep(2.0)
        # 上传后确认弹窗出现，点弹窗内「发送」
        await self._click_dialog_send()
        await self._wait_image_bubble(timeout=15.0)

    async def _click_upload_trigger(self) -> None:
        if hasattr(self.page, "locator"):
            locator = self.page.locator(_s.IMAGE_UPLOAD_TRIGGER).first
            if await locator.count():
                await locator.click(force=True)
                return
            # 兜底：找 IMAGE_INPUTS 任一 input 的父上传区
        raise DomChangedError("未找到图片上传按钮")

    async def _click_dialog_send(self) -> None:
        """点击上传确认弹窗里的「发送」按钮。"""
        if hasattr(self.page, "locator"):
            for selector in _s.IMAGE_DIALOG_SEND:
                locator = self.page.locator(selector).first
                try:
                    if await locator.count() and await locator.is_visible():
                        await locator.click()
                        return
                except Exception:
                    continue
        # 兜底：找文本为「发送」的可点按钮
        if hasattr(self.page, "get_by_text"):
            try:
                await self.page.get_by_text("发送", exact=True).first.click(timeout=3000)
                return
            except Exception:
                pass
        raise DomChangedError("未找到图片确认弹窗的发送按钮")

    async def _count_non_avatar_images(self) -> int:
        """统计「从我的」消息里的非头像图片总数（锚定发送前后，防历史图片误判）。"""
        if hasattr(self.page, "evaluate"):
            try:
                return int(
                    await self.page.evaluate(
                        """() => [...document.querySelectorAll('[class*="contentBox"][class*="isFromMe"] img')]
                            .filter(img => !(img.src || '').includes('avatar')).length"""
                    )
                )
            except Exception:
                return 0
        # FakePage
        return 1 if bool(getattr(self.page, "image_bubble_render", False)) else 0

    async def _media_bubble_snapshot(self) -> tuple[str, ...] | None:
        """读取当前已渲染的「从我的」媒体消息签名。

        抖音消息列表是虚拟列表，新消息插入时旧节点可能被回收，单纯比较
        图片数量会把真实发送误判为未发送。签名只保留消息时间、非头像图片
        的稳定 URL 路径和气泡文本，不保存完整 DOM 或图片内容。
        """
        if not hasattr(self.page, "evaluate"):
            return None
        try:
            result = await self.page.evaluate(
                """({boxSelector, contentSelector, timeSelector}) => {
                    const normalize = (value) => (value || '')
                        .replace(/[\\s\\u200B\\u200C\\u200D\\uFEFF]+/g, ' ')
                        .trim();
                    return [...document.querySelectorAll(boxSelector)]
                        .map((box) => {
                            const content = [...box.querySelectorAll(contentSelector)]
                                .find((node) => String(node.className || '').includes('isFromMe'));
                            if (!content) return null;
                            const images = [...content.querySelectorAll('img')]
                                .filter((img) => !(img.src || '').toLowerCase().includes('avatar'));
                            if (!images.length) return null;
                            const imageKey = images.map((img) => {
                                const src = (img.currentSrc || img.src || '').split(/[?#]/, 1)[0];
                                return `${String(img.className || '')}:${src}`;
                            }).join('|');
                            const time = box.querySelector(timeSelector)?.textContent || '';
                            return `${normalize(time)}\\u001f${imageKey}\\u001f${normalize(content.innerText)}`;
                        })
                        .filter((value) => value !== null);
                }""",
                {
                    "boxSelector": _s.MEDIA_MESSAGE_BOXES,
                    "contentSelector": _s.MEDIA_MESSAGE_CONTENT,
                    "timeSelector": _s.MESSAGE_TIME,
                },
            )
            if isinstance(result, list):
                return tuple(str(value) for value in result)
        except Exception:
            return None
        return None

    async def _trigger_send(self) -> None:
        """优先点发布按钮，Enter 兜底（与文本发送一致）。"""
        if hasattr(self.page, "locator"):
            for selector in _s.SEND_BUTTONS:
                locator = self.page.locator(selector).first
                try:
                    if await locator.count() and await locator.is_visible():
                        await locator.click()
                        return
                except Exception:
                    continue
        await self._press_key("Enter")

    async def _wait_image_bubble(self, timeout: float) -> None:
        """等待「本次发出的图片气泡」真实出现。

        正确性要求（unmev 思路 + 本机 DOM 核实）：
        - 只统计「从我的」消息盒（isFromMe），排除对方消息；
        - 只把「非头像 img」（src 不含 avatar）当作图片气泡，头像会被误判；
        - 优先比较发送前后的媒体消息签名，兼容虚拟列表回收旧节点；
        - 没有签名能力时才回退到发送前后的图片数量。
        满足才置 last_bubble_seen=True；否则视为图片未真正发出，交给 manager 判 unknown。
        """
        self.last_bubble_seen = await self._wait_media_bubble(timeout)

    async def _wait_media_bubble(self, timeout: float) -> bool:
        """轮询直到发现新增媒体气泡；测试假页面直接使用注入标记。"""
        if hasattr(self.page, "image_bubble_render"):
            return bool(self.page.image_bubble_render)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self.image_bubble_confirmed():
                return True
            await self._sleep(0.5)
        return False

    async def image_bubble_confirmed(self, before_count: int | None = None) -> bool:
        """检测「从我的」消息里是否出现了本次新增的非头像媒体气泡。

        优先用消息签名识别新增媒体消息，避免虚拟列表维持固定图片数量时
        把真实发送误报为未发送；无法读取签名时回退到原有数量锚定逻辑。
        """
        # FakePage：以 image_bubble_render 标记为准（真实 Playwright page 无此属性）
        if hasattr(self.page, "image_bubble_render"):
            return bool(self.page.image_bubble_render)
        if hasattr(self.page, "evaluate"):
            try:
                before_snapshot = getattr(self, "_media_before_snapshot", None)
                if before_snapshot is not None:
                    current_snapshot = await self._media_bubble_snapshot()
                    if _has_new_media_bubble(before_snapshot, current_snapshot):
                        return True
                    if current_snapshot is not None:
                        return False
                before = int(before_count if before_count is not None else getattr(self, "_image_before_count", 0))
                cur = int(
                    await self.page.evaluate(
                        """() => [...document.querySelectorAll('[class*="contentBox"][class*="isFromMe"] img')]
                            .filter(img => !(img.src || '').includes('avatar')).length"""
                    )
                )
                # 本次发送后非头像图片数 > 发送前 → 本次的图片气泡已渲染（防历史图片误判）
                return cur > before
            except Exception:
                return False
        return False

    # ------------------------------------------------------------ 表情(sticker)

    async def _first_visible_locator(self, selectors: tuple[str, ...]):
        """返回第一个可见的 locator；找不到返回 None。"""
        if not hasattr(self.page, "locator"):
            return None
        for selector in selectors:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    async def send_sticker(self, sticker: Sticker) -> None:
        """发送抖音原生表情：点表情按钮 → 打开面板 → 按名称选中表情 → 一点即发。

        参考 unmev/douyin-auto-fire 的 send_douyin_sticker()：
        - 抖音原生表情通常一点即发出（无需再点发送按钮）；
        - 若没有一点即发（发布按钮仍在），则点发布按钮兜底；
        - 终态确认 = 「从我的 + 非头像」新增图片气泡（复用图片数量锚定）：
          表情是图片贴纸，与图片一样锚定非头像增图，绝不把头像/旧图当成功；
        - 无论成败，finally 里关闭表情面板并把焦点点回输入框（unmev issue #4：
          面板不关会挡住后续消息的输入框与发送按钮）。
        """
        self.last_bubble_seen = False
        self._image_before_count = await self._count_non_avatar_images()
        self._media_before_snapshot = await self._media_bubble_snapshot()
        if not hasattr(self.page, "locator"):
            raise DomChangedError("页面对象不支持表情选择器")
        try:
            button = await self._first_visible_locator(_s.STICKER_BUTTONS)
            if button is None:
                raise DomChangedError("未找到表情按钮")
            await button.click(force=True)
            panel = await self._first_visible_locator(_s.STICKER_PANELS)
            if panel is None:
                raise DomChangedError("未找到表情面板")
            await self._select_sticker_in_panel(panel, sticker)
        finally:
            await self._restore_composer()

    async def _select_sticker_in_panel(self, panel, sticker: Sticker) -> None:
        # 面板打开后表情项是异步渲染的，先等它们出现再遍历（否则 count=0 找不到）
        await self._wait_sticker_items(panel)
        name = sticker.label or sticker.name
        # 分类：先点面板里的分类标签（可选）
        if getattr(panel, "get_by_text", None) and sticker.category:
            try:
                category = panel.get_by_text(sticker.category, exact=True)
                if await category.count() and await category.first.is_visible():
                    await category.first.click()
            except Exception:
                pass
        # 首选：在表情项容器里按名称节点精确匹配
        try:
            items = panel.locator(_s.STICKER_ITEM)
            for index in range(await items.count()):
                candidate = items.nth(index)
                desc = candidate.locator(_s.STICKER_ITEM_NAME)
                if await desc.count() and (await desc.first.inner_text()).strip() == name:
                    await self._click_sticker(candidate)
                    return
        except SendOutcomeUnknownError:
            raise
        except Exception:
            pass
        # 兜底：role/aria-label/title/alt
        for expr in (
            f'[role="img"][aria-label="{name}"]',
            f'img[aria-label="{name}"]',
            f'button[aria-label="{name}"]',
            f'[aria-label="{name}"]',
            f'[title="{name}"]',
            f'[alt="{name}"]',
        ):
            try:
                candidate = panel.locator(expr).first
                if await candidate.count() and await candidate.is_visible():
                    await self._click_sticker(candidate)
                    return
            except SendOutcomeUnknownError:
                raise
            except Exception:
                continue
        # 兜底索引：面板内第 fallback_index 个可点元素
        if sticker.fallback_index is not None:
            try:
                slots = panel.locator('[role="button"], img, [aria-label], [title]')
                if await slots.count() > sticker.fallback_index:
                    await self._click_sticker(slots.nth(sticker.fallback_index))
                    return
            except SendOutcomeUnknownError:
                raise
            except Exception:
                pass
        raise DomChangedError(f"表情面板中找不到表情: {sticker.name}")

    async def _click_sticker(self, candidate) -> None:
        await candidate.click(force=True)
        await self._sleep(0.8)
        if await self._wait_sticker_bubble(timeout=15.0):
            self.last_bubble_seen = True
            return
        # 少数页面版本会把表情暂存在编辑器，只有发送按钮明确可用时才兜底；
        # 不再无条件按 Enter，避免「点击其实已发送但确认漏检」造成重复发送。
        if await self._trigger_pending_send():
            if await self._wait_sticker_bubble(timeout=15.0):
                self.last_bubble_seen = True
                return
        raise SendOutcomeUnknownError("表情已点击，但未能确认进入会话；已阻止自动重试")

    async def _trigger_pending_send(self) -> bool:
        """仅在发布按钮明确可用时发送待发布表情，避免无条件重复触发。"""
        if not hasattr(self.page, "locator"):
            return False
        for selector in _s.SEND_BUTTONS:
            locator = self.page.locator(selector).first
            try:
                if not await locator.count() or not await locator.is_visible():
                    continue
                is_enabled = getattr(locator, "is_enabled", None)
                if callable(is_enabled) and not await is_enabled():
                    continue
                await locator.click()
                return True
            except Exception:
                continue
        return False

    async def _restore_composer(self) -> None:
        """关闭表情面板并把焦点点回聊天输入框（unmev issue #4 的经验）。"""
        page = self.page
        if hasattr(page, "keyboard"):
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
        try:
            _selector, editor = await self._wait_for_chat_editor(timeout=1.0)
            if editor is not None and hasattr(editor, "click"):
                await editor.click()
        except Exception:
            pass

    async def _wait_sticker_items(self, panel, timeout: float = 5.0) -> None:
        """等待表情面板里的表情项渲染出来（异步），避免 count=0 导致找不到。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                if await panel.locator(_s.STICKER_ITEM).count() > 0:
                    return
            except Exception:
                pass
            await self._sleep(0.3)

    async def _wait_sticker_bubble(self, timeout: float) -> bool:
        """轮询「从我的 + 非头像」新增图片气泡（与图片/表情确认一致的锚定）。

        不用图片专用的 _wait_image_bubble：其 wait_for_function 依赖
        messageMessageBoxcontentBox，表情气泡未必匹配；改用 image_bubble_confirmed
        主动查询（contentBox + isFromMe + 非头像 img，cur>before），真机已验证能命中。
        """
        return await self._wait_media_bubble(timeout)


async def read_self_nickname(page) -> str | None:
    """登录后读取当前抖音身份昵称（M8 后续增强）。

    数据源为 localStorage 的 user_info（登录态写入，比 DOM class 稳定、
    不受页面改版影响）；只提取 nickname 字段，其余信息一律不读取。
    未登录 / 键不存在 / 结构变化 / 解析失败一律返回 None（纯展示用途，
    绝不影响登录判定与发送链路）。
    """
    try:
        raw = await page.evaluate(
            "() => { try { return localStorage.getItem('user_info'); }"
            " catch { return null; } }"
        )
        if not raw:
            return None
        data = json.loads(raw)
        if isinstance(data, dict):
            nickname = data.get("nickname")
            if isinstance(nickname, str) and nickname.strip():
                return nickname.strip()
    except Exception:  # noqa: BLE001 - 展示性信息，任何失败都静默降级
        return None
    return None


async def read_qr_data_url(page) -> str | None:
    """登录弹窗中的二维码图片 data URL（M8-4 截图扫码的数据源）。

    二维码在页面里是 img[src^="data:image/png;base64,..."]（约 178px 见方）。
    用「data:image 前缀 + 边长 120-260」的结构启发式定位，不依赖被混淆的
    class 名；找不到或结构变化返回 None（调用方退回整页截图兜底）。
    """
    try:
        result = await page.evaluate(
            """() => {
              for (const img of document.querySelectorAll('img')) {
                const src = img.src || '';
                if (!src.startsWith('data:image')) continue;
                const r = img.getBoundingClientRect();
                if (r.width >= 120 && r.width <= 260 && r.height >= 120 && r.height <= 260) {
                  return src;
                }
              }
              return null;
            }"""
        )
        if isinstance(result, str) and result.startswith("data:image/png;base64,"):
            return result
    except Exception:  # noqa: BLE001 - 展示性数据，任何失败都静默降级
        return None
    return None
