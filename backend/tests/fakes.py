"""测试用假浏览器实现：只覆盖 DouyinChat 使用到的页面协议。"""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.execution.browser_manager import BrowserManager
from app.execution.selectors import (
    CHAT_EDITOR,
    CONVERSATION_ITEM,
    CONVERSATION_LIST,
    CONVERSATION_TITLE,
    IMAGE_INPUTS,
    RIGHT_TITLE,
    SEARCH_CHAT_BTN,
    SEARCH_INPUT,
    SEARCH_RESULT_ITEM,
    SEND_FAILURE_MARKERS,
)
from app.main import create_app


class FakeElement:
    def __init__(self, text, on_click=None):
        self._text = text
        self._on_click = on_click

    async def inner_text(self) -> str:
        value = self._text() if callable(self._text) else self._text
        return value

    async def click(self, timeout: int | None = None) -> None:
        if self._on_click is not None:
            self._on_click()

    async def set_input_files(self, path: str) -> None:
        self.uploaded_path = path


class _FakeIdentityResponse:
    def __init__(self, payload: dict):
        self.url = "https://www.douyin.com/aweme/v1/web/im/user/info/"
        self._payload = payload

    async def json(self):
        return self._payload


class FakeItem:
    def __init__(self, page: "FakePage", title: str, search_style: bool = False):
        self._page = page
        self._title = title
        self._search_style = search_style

    async def query_selector(self, selector: str):
        if selector == CONVERSATION_TITLE:
            return None if self._search_style else FakeElement(self._title)
        if selector == SEARCH_CHAT_BTN:
            if not self._search_style:
                return None
            return FakeElement(
                "发消息",
                on_click=lambda: self._page._on_conversation_click(self._title),
            )
        return None

    async def inner_text(self) -> str:
        if self._search_style:
            return f"{self._title}\n发消息"
        return self._title

    async def click(self, timeout: int | None = None) -> None:
        self._page.clicked.append((self._title, "row"))


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
        search_results: dict[str, tuple[str, ...]] | None = None,
        titles_revealed_by_scroll: tuple[str, ...] = (),
        search_requires_warmup: bool = False,
        search_results_after_polls: int = 0,
        identity_payloads: dict[str, dict] | None = None,
        image_bubble_render: bool = False,
        sticker_items: list[str] = (),
        sticker_button_available: bool = True,
        sticker_panel_available: bool = True,
    ):
        self.logged_in = logged_in
        self._become_logged_after = become_logged_after
        self._list_polls = 0
        self._original_titles = list(conversation_titles)
        self.conversation_titles = list(conversation_titles)
        self.right_title = right_title
        self.editor_present = editor_present
        self.failure_marker = failure_marker
        self.clear_on_enter = clear_on_enter
        self.search_results = search_results or {}
        self.titles_revealed_by_scroll = list(titles_revealed_by_scroll)
        self.search_requires_warmup = search_requires_warmup
        self.search_results_after_polls = search_results_after_polls
        self.identity_payloads = identity_payloads or {}
        self._handlers: dict[str, list] = {}
        self._search_polls = 0
        self.warmed = False
        self.search_active = False
        self.editor_text = ""
        self.gotos: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.typed: list[tuple[str, str]] = []
        self.keys: list[str] = []
        self.scrolls: list[str] = []
        self.clicked: list[tuple[str, str]] = []
        self.closed = False
        self.image_input_log: list[str] = []
        self.image_bubble_render = image_bubble_render
        self.image_uploaded = False
        self.attachment_clicked = False
        self.sticker_items = list(sticker_items)
        self.sticker_button_available = sticker_button_available
        self.sticker_panel_available = sticker_panel_available
        self.sticker_clicked = None
        self.url = "https://www.douyin.com/chat"
        self.screenshots: list[str] = []
        self.goto_kwargs: list[dict] = []
        # 登录二维码：真实页面渲染完成前为 None，这里默认视为已渲染出二维码
        self.qr_data_url = "data:image/png;base64,AAAA"
        # evaluate 桩：设置后 page.evaluate 返回该值（读自身昵称测试用）
        self.evaluate_result = None

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def _on_conversation_click(self, title: str) -> None:
        self.clicked.append((title, "chat_btn"))
        payload = self.identity_payloads.get(title)
        if payload is not None:
            asyncio.create_task(self.emit_response(_FakeIdentityResponse(payload)))

    async def emit_response(self, response) -> None:
        for handler in self._handlers.get("response", []):
            handler(response)
        await asyncio.sleep(0)

    async def screenshot(self, path=None, full_page=False, **kwargs):
        """记录截图调用并落一个占位文件（诊断截图路径断言用）。"""
        self.screenshots.append(str(path) if path else None)
        if path:
            Path(path).write_bytes(b"fake-png")
        return path

    async def goto(self, url: str, **kwargs) -> None:
        self.gotos.append(url)
        self.goto_kwargs.append(kwargs)

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
        if selector in IMAGE_INPUTS:
            element = FakeElement("")
            element.set_input_files = self._set_image_input  # type: ignore[method-assign]
            return element
        if selector in SEND_FAILURE_MARKERS:
            return FakeElement("") if self.failure_marker else None
        return None

    async def _set_image_input(self, path: str) -> None:
        self.image_input_log.append(path)

    # --- 图片上传 fake API（匹配真实 Playwright 图片发送流程）---
    def expect_file_chooser(self, timeout: int | None = None):
        import contextlib

        chooser = _FakeFileChooser(self)

        @contextlib.asynccontextmanager
        async def _cm():
            class _Info:
                @property
                def value(self):
                    async def _resolve():
                        return chooser

                    return _resolve()

            yield _Info()

        return _cm()

    def locator(self, selector: str):
        return _FakeLocator(self, selector)

    def get_by_text(self, text: str, exact: bool = False):
        return _FakeLocator(self, text, by_text=True)

    async def query_selector_all(self, selector: str):
        if selector == SEARCH_RESULT_ITEM:
            if not self.search_active:
                return []
            self._search_polls += 1
            if self._search_polls <= self.search_results_after_polls:
                return []  # 模拟结果延迟渲染
            return [FakeItem(self, title, search_style=True) for title in self.conversation_titles]
        if selector == CONVERSATION_ITEM:
            if self.search_active:
                return []
            return [FakeItem(self, title) for title in self.conversation_titles]
        return []

    async def fill(self, selector: str, value: str) -> None:
        self.fills.append((selector, value))
        if selector == SEARCH_INPUT:
            if value.strip():
                self.search_active = True
                if self.search_requires_warmup and not self.warmed:
                    self.conversation_titles = []
                else:
                    self.conversation_titles = list(
                        self.search_results.get(value.strip(), self._original_titles)
                    )
            else:
                self.search_active = False
                self.conversation_titles = list(self._original_titles)

    async def type_text(self, selector: str, text: str) -> None:
        self.typed.append((selector, text))
        if selector == CHAT_EDITOR:
            self.editor_text += text

    async def press_key(self, key: str) -> None:
        self.keys.append(key)
        if key == "Enter" and self.editor_text.strip() and self.clear_on_enter:
            self.editor_text = ""

    async def evaluate(self, _expression: str, target=None, *args, **kwargs):
        # 登录二维码查询脚本（结构与 class 无关，只认 data:image 前缀）
        if "data:image" in _expression:
            return self.qr_data_url
        # 读取自身昵称等「取值型」evaluate：测试显式设置 evaluate_result 时优先返回
        if self.evaluate_result is not None:
            return self.evaluate_result
        # 真实实现传入 CONVERSATION_LIST 的元素句柄；这里不区分句柄/选择器，
        # 只要发生滚动就视为已预热并揭示懒加载标题。
        self.scrolls.append("evaluate")
        self.warmed = True
        for title in self.titles_revealed_by_scroll:
            if title not in self.conversation_titles:
                self.conversation_titles.append(title)
        return None

    async def evaluate_js(self, _script: str) -> dict:
        return {}


class FakeKeyboard:
    """模拟真实 Playwright page.keyboard：只有 type/press。"""

    def __init__(self, page: "RealApiPage"):
        self._page = page

    async def type(self, text: str, delay: int = 0) -> None:
        self._page.editor_text += text
        self._page.typed.append(text)

    async def press(self, key: str) -> None:
        self._page.keys.append(key)
        if key == "Enter" and self._page.editor_text.strip() and self._page.clear_on_enter:
            self._page.editor_text = ""


class RealApiPage(FakePage):
    """形状与真实 Playwright Page 一致：没有 type_text/press_key 协议方法。

    用于回归测试「假页面协议不得泄漏到真实实现」——代码若误调，将直接 AttributeError。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.keyboard = FakeKeyboard(self)


class FakeContext:
    def __init__(self, page: FakePage):
        self.page = page

    async def close(self) -> None:
        self.page.closed = True


class _FakeFileChooser:
    """模拟 Playwright FileChooser，set_files 记录到 page.image_input_log。"""

    def __init__(self, page: FakePage):
        self._page = page

    async def set_files(self, path: str) -> None:
        self._page.image_input_log.append(path)
        # 图片注入后模拟上传完成（供确认弹窗出现）
        self._page.image_uploaded = True


class _FakeLocator:
    """模拟 Playwright Locator：能 count/click/is_visible/nth/inner_text，按 selector 匹配。

    支持图片上传（.semi-upload-add）、确认弹窗「发送」、以及表情发送
    （表情按钮/表情面板/表情项/表情项描述名），供 DouyinChat.send_sticker 走通。
    其它 selector 返回空（count=0）。
    """

    def __init__(self, page: FakePage, selector: str, by_text: bool = False, index: int | None = None):
        self._page = page
        self._selector = selector
        self._by_text = by_text
        self._index = index

    async def count(self) -> int:
        if self._by_text:
            return 1 if self._selector == "发送" else 0
        # 表情按钮
        if "messageMsgInputiconAction" in self._selector or "表情" in self._selector or "[title*=\"表情\"]" in self._selector:
            return 1 if self._page.sticker_button_available else 0
        # 表情面板
        if "componentsemojiemojiPanel" in self._selector or "emojiPanel" in self._selector or "[class*=\"sticker\"]" in self._selector:
            return 1 if self._page.sticker_panel_available else 0
        # 表情项容器
        if "emojiEmojiItememojiItem" in self._selector and "Desc" not in self._selector:
            return len(self._page.sticker_items)
        # 表情项描述名（需绑定 nth index）
        if "emojiEmojiItememojiItemDesc" in self._selector:
            return 1 if (self._index is not None and self._index < len(self._page.sticker_items)) else 0
        # 附件图标 / 确认弹窗发送按钮 → 可点；否则 0
        if "semi-upload-add" in self._selector:
            return 1
        if any(tok in self._selector for tok in ("primary", "semi-button", "confirm")):
            return 1 if self._page.image_uploaded else 0
        return 0

    async def is_visible(self) -> bool:
        return await self.count() > 0

    async def click(self, force: bool = False, timeout: int | None = None) -> None:
        if self._by_text or "semi-upload-add" in self._selector:
            self._page.attachment_clicked = True
        if "emojiEmojiItememojiItem" in self._selector and "Desc" not in self._selector:
            self._page.sticker_clicked = (
                self._page.sticker_items[self._index] if self._index is not None else True
            )
            # 注意：这里不强制设置 image_bubble_render——「气泡是否出现」由测试设定，
            # 以便能覆盖「表情点了但没发出（失败）」与「一点即发（成功）」两种场景。
        return None

    async def inner_text(self) -> str:
        if "emojiEmojiItememojiItemDesc" in self._selector and self._index is not None:
            return self._page.sticker_items[self._index]
        return ""

    def locator(self, selector: str):
        # panel.locator(item) / candidate.locator(desc)：带上当前 index 形成子树查询
        # 注意：Playwright Locator.locator() 是同步方法，故本处不能是 async。
        return _FakeLocator(self._page, selector, index=self._index)

    @property
    def first(self):
        return self

    def nth(self, index: int):
        return _FakeLocator(self._page, self._selector, by_text=self._by_text, index=index)


def make_factory(pages: list[FakePage]):
    queue = list(pages)

    async def factory(_profile_path, _headless):
        page = queue.pop(0)
        return FakeContext(page), page

    return factory


def make_app(settings, pages: list[FakePage]):
    engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)

    async def _online() -> bool:
        return True  # 测试默认网络可用（真实探测由 BrowserManager._default_network_ok 实现）

    manager = BrowserManager(
        engine,
        settings,
        browser_factory=make_factory(pages),
        login_poll_interval=0.02,
        login_timeout=1.0,
        login_check_timeout=0.2,
        send_timeout=0.2,
        send_poll_interval=0.05,
        send_stabilize_polls=2,
        confirm_timeout=0.2,
        search_timeout=0.2,
        network_checker=_online,
        sleep=lambda _seconds: asyncio.sleep(0),
    )
    return create_app(settings, browser_manager=manager)


def create_account(client: TestClient, name: str = "测试账号") -> dict:
    response = client.post("/api/accounts", json={"displayName": name})
    assert response.status_code == 201, response.text
    return response.json()
