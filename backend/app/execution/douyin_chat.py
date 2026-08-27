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
    SEARCH_INPUTS,
    SEARCH_NAME_SELECTORS,
    SEARCH_RESULT_ITEM,
    SEND_FAILURE_MARKERS,
)

MAX_LIST_SCROLLS = 8
WARMUP_SCROLLS = 5
SEARCH_RESULTS_TIMEOUT = 12.0
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
        search_timeout: float = SEARCH_RESULTS_TIMEOUT,
        sleep=asyncio.sleep,
    ):
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirm_timeout = confirm_timeout
        self.search_timeout = search_timeout
        self._sleep = sleep
        self._warmed = False

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
        await self._fill_first(SEARCH_INPUTS, name)
        await self._sleep(2.2)

    async def _fill_first(self, selectors: tuple[str, ...], value: str) -> None:
        for selector in selectors:
            if await self.page.query_selector(selector) is not None:
                await self.page.fill(selector, value)
                return

    async def warm_up_list(self) -> None:
        """滚动列表触发懒加载：会话列表与搜索索引都是分页拉取的。

        刚打开页面时列表为空、搜索索引也空（搜什么都是“未搜索到相关内容”）；
        必须先滚动让数据加载，搜索才有结果。
        """
        if self._warmed:
            return
        for _ in range(WARMUP_SCROLLS):
            await self.page.evaluate("(el) => { el.scrollTop += 800 }", CONVERSATION_LIST)
            await self._sleep(0.8)
        self._warmed = True

    async def open_conversation(self, name: str) -> ConversationOpen:
        # 0) 预热：先滚动列表，让懒加载数据与搜索索引就绪
        try:
            await self.warm_up_list()
        except Exception:
            pass
        # 1) 搜索优先：直接搜索昵称，从结果中精确匹配
        try:
            await self.search(name)
            result = await self._search_and_open(name)
            if result is not None:
                return result
        except Exception:
            pass
        # 2) 清空搜索，滚动加载列表兜底（会话列表可能是懒加载）
        try:
            await self._fill_first(SEARCH_INPUTS, "")
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
            await self._fill_first(SEARCH_INPUTS, "")
        except Exception:
            pass
        return result

    async def _search_and_open(self, name: str) -> ConversationOpen | None:
        """搜索后轮询等待结果渲染（首次加载时结果可能晚于固定等待时间）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.search_timeout
        while True:
            result = await self._scan_items(name)
            if result.opened:
                return result
            if loop.time() >= deadline:
                break
            await self._sleep(1.0)
        if await self._try_click_exact_by_text(name):
            return await self._confirm_opened(name)
        return None

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
