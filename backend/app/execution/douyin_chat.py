import asyncio
from dataclasses import dataclass

from .selectors import (
    CHAT_EDITOR,
    CHAT_URL,
    CONVERSATION_ITEM,
    CONVERSATION_LIST,
    CONVERSATION_TITLE,
    RIGHT_TITLE,
    SEARCH_CHAT_BTN,
    SEARCH_INPUT,
    SEARCH_NAME_SELECTORS,
    SEARCH_RESULT_ITEM,
    SEND_FAILURE_MARKERS,
)

MAX_LIST_SCROLLS = 8
_ITEM_SELECTORS = (CONVERSATION_ITEM, SEARCH_RESULT_ITEM)
OPEN_CONFIRM_TIMEOUT = 8.0


class DomChangedError(RuntimeError):
    """关键页面结构不存在，说明抖音页面已改版。"""


@dataclass
class ConversationOpen:
    opened: bool
    title: str | None
    exact: bool


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
        sleep=asyncio.sleep,
    ):
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirm_timeout = confirm_timeout
        self._sleep = sleep

    async def open_chat_page(self) -> None:
        await self.page.goto(CHAT_URL)
        await self._sleep(3)

    async def is_logged_in(self) -> bool:
        element = await self.page.query_selector(CONVERSATION_LIST)
        return element is not None

    async def right_panel_title(self) -> str | None:
        element = await self.page.query_selector(RIGHT_TITLE)
        if element is None:
            return None
        text = await element.inner_text()
        return text.strip() if text else None

    async def search(self, name: str) -> None:
        await self.page.fill(SEARCH_INPUT, name)
        await self._sleep(1.2)

    async def open_conversation(self, name: str) -> ConversationOpen:
        # 1) 搜索优先：直接搜索昵称，从结果中精确匹配
        try:
            await self.page.fill(SEARCH_INPUT, name)
            await self._sleep(1.5)
            result = await self._scan_items(name)
            if result.opened:
                return result
        except Exception:
            pass
        # 2) 清空搜索，滚动加载列表兜底（会话列表可能是懒加载）
        try:
            await self.page.fill(SEARCH_INPUT, "")
            await self._sleep(0.8)
        except Exception:
            pass
        for _ in range(MAX_LIST_SCROLLS):
            result = await self._scan_items(name)
            if result.opened:
                return result
            await self.page.evaluate("(el) => { el.scrollTop += 800 }", CONVERSATION_LIST)
            await self._sleep(0.8)
        return ConversationOpen(opened=False, title=None, exact=False)

    async def _scan_items(self, name: str) -> ConversationOpen:
        for selector in _ITEM_SELECTORS:
            items = await self.page.query_selector_all(selector)
            for item in items:
                title = await self._title_of(item)
                if title != name:
                    continue
                target = await item.query_selector(SEARCH_CHAT_BTN)
                if target is None:
                    target = item
                await target.click()
                return await self._confirm_opened(name)
        return ConversationOpen(opened=False, title=None, exact=False)

    async def _confirm_opened(self, name: str) -> ConversationOpen:
        """点击后轮询右侧标题确认（SPA 切换有延迟，同 unmev 的 _confirm_opened）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.confirm_timeout
        right_title: str | None = None
        while True:
            right_title = await self.right_panel_title()
            if right_title == name:
                return ConversationOpen(opened=True, title=right_title, exact=True)
            if loop.time() >= deadline:
                return ConversationOpen(opened=True, title=right_title, exact=False)
            await self._sleep(0.5)

    async def _title_of(self, item) -> str | None:
        """按 unmev 的顺序取标题：搜索行名称节点 → 会话标题子元素 → 整行首行兜底。"""
        for selector in SEARCH_NAME_SELECTORS:
            element = await item.query_selector(selector)
            if element is not None:
                text = await element.inner_text()
                if text and text.strip():
                    return text.strip()
        title_element = await item.query_selector(CONVERSATION_TITLE)
        if title_element is not None:
            text = await title_element.inner_text()
            if text and text.strip():
                return text.strip()
        text = await item.inner_text()
        if not text:
            return None
        first_line = text.splitlines()[0].strip()
        return first_line or None

    async def visible_titles(self, limit: int = 40) -> list[str]:
        titles: list[str] = []
        seen: set[str] = set()
        for selector in _ITEM_SELECTORS:
            items = await self.page.query_selector_all(selector)
            for item in items:
                title_element = await item.query_selector(CONVERSATION_TITLE)
                if title_element is None:
                    continue
                title = ((await title_element.inner_text()) or "").strip()
                if title and title not in seen:
                    seen.add(title)
                    titles.append(title)
                    if len(titles) >= limit:
                        return titles
        return titles

    async def send_text(self, text: str) -> None:
        editor = await self.page.query_selector(CHAT_EDITOR)
        if editor is None:
            raise DomChangedError("未找到消息输入框")
        await editor.click()
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line:
                await self.page.type_text(CHAT_EDITOR, line)
            if index < len(lines) - 1:
                await self.page.press_key("Shift+Enter")
        await self.page.press_key("Enter")

    async def editor_is_cleared(self) -> bool:
        element = await self.page.query_selector(CHAT_EDITOR)
        if element is None:
            return True
        text = await element.inner_text()
        return not (text or "").strip()

    async def failure_marker_visible(self) -> bool:
        for selector in SEND_FAILURE_MARKERS:
            if await self.page.query_selector(selector) is not None:
                return True
        return False
