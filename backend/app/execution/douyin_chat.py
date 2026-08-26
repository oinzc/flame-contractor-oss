import asyncio
from dataclasses import dataclass

from .selectors import (
    CHAT_URL,
    CONVERSATION_ITEM,
    CONVERSATION_LIST,
    CONVERSATION_TITLE,
    RIGHT_TITLE,
    SEARCH_INPUT,
)


@dataclass
class ConversationOpen:
    opened: bool
    title: str | None
    exact: bool


class DouyinChat:
    """对单个 Playwright page 的窄封装；所有页面交互集中在这里。

    page 只需实现 goto/query_selector/query_selector_all/fill/click/inner_text，
    便于在测试中注入假实现，也便于未来替换执行后端。
    """

    def __init__(self, page, timeout_ms: int = 15_000, sleep=asyncio.sleep):
        self.page = page
        self.timeout_ms = timeout_ms
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
        items = await self.page.query_selector_all(CONVERSATION_ITEM)
        for item in items:
            title_element = await item.query_selector(CONVERSATION_TITLE)
            if title_element is None:
                continue
            title = ((await title_element.inner_text()) or "").strip()
            if title == name:
                await item.click()
                await self._sleep(1.2)
                right_title = await self.right_panel_title()
                return ConversationOpen(
                    opened=True,
                    title=right_title,
                    exact=right_title == name,
                )
        return ConversationOpen(opened=False, title=None, exact=False)
