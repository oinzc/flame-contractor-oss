import asyncio
import json
import re
from dataclasses import dataclass

# 关键：必须用「模块引用 + 运行期取值」，不能在导入时 `from .selectors import X`
# 捕获常量——那样 selector 热更新对这个文件就失效了。
from . import selectors as _s

# 抖音输入框发送后会残留零宽字符，普通 strip() 判不清空
_BLANK_RE = re.compile(r"[\s\u200b\u200c\u200d\ufeff]+")

MAX_LIST_SCROLLS = 8
WARMUP_SCROLLS = 5
SEARCH_RESULTS_TIMEOUT = 12.0
OPEN_CONFIRM_TIMEOUT = 8.0
# 全量扫描会话列表：滚动轮数上限 + 连续无新增即判定到底
SCAN_MAX_ROUNDS = 80
SCAN_IDLE_ROUNDS = 3


def _item_selectors() -> tuple:
    """运行期返回「会话列表项 + 搜索结果行」两组选择器（随 bundle 热更）。"""
    return (_s.CONVERSATION_ITEM, _s.SEARCH_RESULT_ITEM)


class DomChangedError(RuntimeError):
    """关键页面结构不存在，说明抖音页面已改版。"""


@dataclass
class ConversationOpen:
    opened: bool
    title: str | None
    exact: bool


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

    async def open_chat_page(self) -> None:
        await self.page.goto(_s.CHAT_URL)
        await self._sleep(3)

    async def is_logged_in(self) -> bool:
        element = await self.page.query_selector(_s.CONVERSATION_LIST)
        return element is not None

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
        while True:
            if await self.is_logged_in():
                return True
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
            await self._fill_first(_s.SEARCH_INPUTS, "")
            await self._sleep(0.8)
        except Exception:
            pass
        for _ in range(MAX_LIST_SCROLLS):
            result = await self._scan_items(name)
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
                return await self._confirm_opened(name)
        return ConversationOpen(opened=False, title=None, exact=False)

    async def _confirm_opened(self, name: str) -> ConversationOpen:
        """点击后轮询右侧标题确认（SPA 切换有延迟，同 unmev 的 _confirm_opened）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.confirm_timeout
        right_title: str | None = None
        while True:
            right_title = await self.right_panel_title()
            if self._primary_panel_title(right_title) == name:
                return ConversationOpen(opened=True, title=right_title, exact=True)
            if loop.time() >= deadline:
                return ConversationOpen(opened=True, title=right_title, exact=False)
            await self._sleep(0.5)

    @staticmethod
    def _primary_panel_title(value: str | None) -> str | None:
        """取右侧标题中的主昵称，忽略官方火花徽标占用的后续行。

        抖音会把「正常天数」「重燃中 X/3」「X 天后消失」等状态追加到
        右侧标题文本中。主昵称仍必须是首个非空行的严格匹配，不能退化为
        模糊包含，以维持「宁可不发不错发」的发送安全边界。
        """
        if not value:
            return None
        for line in value.splitlines():
            primary = line.strip()
            if primary:
                return primary
        return None

    async def _title_of(self, item) -> str | None:
        """按 unmev 的顺序取标题：搜索行名称节点 → 会话标题子元素 → 整行首行兜底。"""
        for selector in _s.SEARCH_NAME_SELECTORS:
            element = await item.query_selector(selector)
            if element is not None:
                text = await element.inner_text()
                if text and text.strip():
                    return text.strip()
        title_element = await item.query_selector(_s.CONVERSATION_TITLE)
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
        for selector in _item_selectors():
            items = await self.page.query_selector_all(selector)
            for item in items:
                title_element = await item.query_selector(_s.CONVERSATION_TITLE)
                if title_element is None:
                    continue
                title = ((await title_element.inner_text()) or "").strip()
                if title and title not in seen:
                    seen.add(title)
                    titles.append(title)
                    if len(titles) >= limit:
                        return titles
        return titles

    async def scan_all_chats(self) -> list[dict]:
        """全量扫描会话列表：边滚动边收集去重，连续多轮无新增即判定到底。

        只读操作——不点击任何会话、不发送任何消息；供好友批量导入挑选候选。
        返回结构化条目：title=会话名，preview=最后一条消息，
        streak_days=抖音官方火花天数（无火花标记为 None——这就是续火对象的判定信号），
        streak_kind=火花类型（normal 普通 / couple 情侣 / family 家人，取自图标路径）。
        """
        entries: list[dict] = []
        seen: set[str] = set()
        scroll_element = await self.page.query_selector(_s.CONVERSATION_LIST)
        if scroll_element is None:
            return entries
        idle = 0
        for _ in range(SCAN_MAX_ROUNDS):
            count_before = len(entries)
            for item in await self.page.query_selector_all(_s.CONVERSATION_ITEM):
                title_element = await item.query_selector(_s.CONVERSATION_TITLE)
                if title_element is None:
                    continue
                title = ((await title_element.inner_text()) or "").strip()
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
            idle = idle + 1 if len(entries) == count_before else 0
            at_bottom = await self.page.evaluate(
                "(el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 5",
                scroll_element,
            )
            if at_bottom or idle >= SCAN_IDLE_ROUNDS:
                break
            await self.page.evaluate("(el) => { el.scrollTop += 900 }", scroll_element)
            await self._sleep(0.8)
        return entries

    async def _type_text(self, text: str) -> None:
        """输入文本：真实页面走 IME 安全的 insert_text；测试协议方法兜底。"""
        page = self.page
        if hasattr(page, "type_text"):
            await page.type_text(_s.CHAT_EDITOR, text)
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
                    """(expected) => {
                        const editors = [
                          ...document.querySelectorAll('[class*="messageEditor"] [contenteditable=true], .messageEditorinputArea, div[data-slate-editor="true"][contenteditable="true"]'),
                        ];
                        return editors.some((e) => (e.innerText || '').includes(expected));
                    }""",
                    arg=content,
                    timeout=5000,
                )
                return True
            except Exception:
                return False
        editor = await self.page.query_selector(_s.CHAT_EDITOR)
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
        editor = await self.page.query_selector(_s.CHAT_EDITOR)
        if editor is None:
            raise DomChangedError("未找到消息输入框")
        await editor.click()
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line:
                await self._type_text(line)
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
        element = await self.page.query_selector(_s.CHAT_EDITOR)
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
        - 以发送前的盒子数为锚，要求新出现一条 isFromMe 且含非头像 img。
        满足才置 last_bubble_seen=True；否则视为图片未真正发出，交给 manager 判 unknown。
        """
        before_count = int(getattr(self, "_image_before_count", 0))
        if hasattr(self.page, "wait_for_function"):
            try:
                await self.page.wait_for_function(
                    """([before]) => {
                        const boxes = [...document.querySelectorAll('[class*="messageMessageBoxcontentBox"][class*="isFromMe"]')];
                        if (boxes.length <= before) return false;
                        const latest = boxes[boxes.length - 1];
                        const hasRealImage = [...latest.querySelectorAll('img')].some(
                            (img) => !(img.src || '').includes('avatar')
                        );
                        return hasRealImage;
                    }""",
                    arg=[before_count],
                    timeout=int(timeout * 1000),
                )
                self.last_bubble_seen = True
            except Exception:
                self.last_bubble_seen = False
            return
        # FakePage：由测试注入图片已出现的标记
        if bool(getattr(self.page, "image_bubble_render", False)):
            self.last_bubble_seen = True
        else:
            self.last_bubble_seen = False

    async def image_bubble_confirmed(self, before_count: int | None = None) -> bool:
        """检测「从我的」消息里是否出现了非头像图片气泡（真实图）。

        与 _wait_image_bubble 不同：这里是主动查询（供 manager 轮询），
        用与实机一致的 selector（contentBox + isFromMe），只认非头像 img。
        before_count 提供时要求「新增一条」；None 则只要出现非头像图即算。
        """
        # FakePage：以 image_bubble_render 标记为准（真实 Playwright page 无此属性）
        if hasattr(self.page, "image_bubble_render"):
            return bool(self.page.image_bubble_render)
        if hasattr(self.page, "evaluate"):
            try:
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
            except Exception:
                continue
        # 兜底索引：面板内第 fallback_index 个可点元素
        if sticker.fallback_index is not None:
            try:
                slots = panel.locator('[role="button"], img, [aria-label], [title]')
                if await slots.count() > sticker.fallback_index:
                    await self._click_sticker(slots.nth(sticker.fallback_index))
                    return
            except Exception:
                pass
        raise DomChangedError(f"表情面板中找不到表情: {sticker.name}")

    async def _click_sticker(self, candidate) -> None:
        await candidate.click(force=True)
        await self._sleep(0.8)
        if await self._wait_sticker_bubble(timeout=15.0):
            self.last_bubble_seen = True
            return
        # 表情可能没有一点即发（发布按钮仍在）→ 点发布按钮兜底再确认
        await self._trigger_send()
        if await self._wait_sticker_bubble(timeout=15.0):
            self.last_bubble_seen = True
            return
        raise DomChangedError("表情已点击但未能确认进入会话")

    async def _restore_composer(self) -> None:
        """关闭表情面板并把焦点点回聊天输入框（unmev issue #4 的经验）。"""
        page = self.page
        if hasattr(page, "keyboard"):
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
        try:
            editor = await page.query_selector(_s.CHAT_EDITOR)
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
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self.image_bubble_confirmed():
                return True
            await self._sleep(0.5)
        return False


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
