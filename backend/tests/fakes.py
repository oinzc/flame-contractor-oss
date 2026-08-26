"""测试用假浏览器实现：只覆盖 DouyinChat 使用到的页面协议。"""

import asyncio

from fastapi.testclient import TestClient

from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.execution.browser_manager import BrowserManager
from app.execution.selectors import (
    CHAT_EDITOR,
    CONVERSATION_ITEM,
    CONVERSATION_LIST,
    CONVERSATION_TITLE,
    RIGHT_TITLE,
    SEARCH_INPUT,
    SEND_FAILURE_MARKERS,
)
from app.main import create_app


class FakeElement:
    def __init__(self, text):
        self._text = text

    async def inner_text(self) -> str:
        value = self._text() if callable(self._text) else self._text
        return value

    async def click(self) -> None:
        pass


class FakeItem:
    def __init__(self, title: str):
        self._title = title

    async def query_selector(self, selector: str):
        if selector == CONVERSATION_TITLE:
            return FakeElement(self._title)
        return None

    async def click(self) -> None:
        pass


class FakePage:
    def __init__(
        self,
        *,
        logged_in: bool = False,
        become_logged_after: int | None = None,
        conversation_titles: tuple[str, ...] = (),
        right_title: str | None = None,
        editor_present: bool = True,
        failure_marker: bool = False,
        clear_on_enter: bool = True,
    ):
        self.logged_in = logged_in
        self._become_logged_after = become_logged_after
        self._list_polls = 0
        self.conversation_titles = list(conversation_titles)
        self.right_title = right_title
        self.editor_present = editor_present
        self.failure_marker = failure_marker
        self.clear_on_enter = clear_on_enter
        self.editor_text = ""
        self.gotos: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.typed: list[tuple[str, str]] = []
        self.keys: list[str] = []
        self.closed = False

    async def goto(self, url: str) -> None:
        self.gotos.append(url)

    async def query_selector(self, selector: str):
        if selector == CONVERSATION_LIST:
            if self._become_logged_after is not None:
                self._list_polls += 1
                if self._list_polls >= self._become_logged_after:
                    self.logged_in = True
            return FakeElement("") if self.logged_in else None
        if selector == RIGHT_TITLE:
            return FakeElement(self.right_title) if self.right_title is not None else None
        if selector == SEARCH_INPUT:
            return FakeElement("")
        if selector == CHAT_EDITOR:
            return FakeElement(lambda: self.editor_text) if self.editor_present else None
        if selector in SEND_FAILURE_MARKERS:
            return FakeElement("") if self.failure_marker else None
        return None

    async def query_selector_all(self, selector: str):
        if selector == CONVERSATION_ITEM:
            return [FakeItem(title) for title in self.conversation_titles]
        return []

    async def fill(self, selector: str, value: str) -> None:
        self.fills.append((selector, value))

    async def type_text(self, selector: str, text: str) -> None:
        self.typed.append((selector, text))
        if selector == CHAT_EDITOR:
            self.editor_text += text

    async def press_key(self, key: str) -> None:
        self.keys.append(key)
        if key == "Enter" and self.editor_text.strip() and self.clear_on_enter:
            self.editor_text = ""


class FakeContext:
    def __init__(self, page: FakePage):
        self.page = page

    async def close(self) -> None:
        self.page.closed = True


def make_factory(pages: list[FakePage]):
    queue = list(pages)

    async def factory(_profile_path, _headless):
        page = queue.pop(0)
        return FakeContext(page), page

    return factory


def make_app(settings, pages: list[FakePage]):
    engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)
    manager = BrowserManager(
        engine,
        settings,
        browser_factory=make_factory(pages),
        login_poll_interval=0.02,
        login_timeout=1.0,
        send_timeout=0.2,
        send_poll_interval=0.05,
        send_stabilize_polls=2,
        sleep=lambda _seconds: asyncio.sleep(0),
    )
    return create_app(settings, browser_manager=manager)


def create_account(client: TestClient, name: str = "测试账号") -> dict:
    response = client.post("/api/accounts", json={"displayName": name})
    assert response.status_code == 201, response.text
    return response.json()
