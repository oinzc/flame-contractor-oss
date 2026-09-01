"""抖音网页版 DOM 适配层 —— 支持热更新的 selector bundle。

选择器概念参考了 MIT 许可的开源项目（unmev/douyin-auto-fire、
dr-190/ScriptCat-Douyin-Fire-Helper、2061360308/DouYinSparkFlow），
按本项目的安全边界重写并集中于此。

【热更新设计】
抖音页面随时可能改版。原实现把选择器硬编码为模块常量，改版必须改源码 + 重启
进程（重启会中断调度器、登录会话与浏览器 Profile 的在线状态）。现改为：

  - 选择器的「运行期唯一来源」是同目录 `selectors_bundle.json`；
  - 访问模块属性（`selectors.CONVERSATION_LIST` 等）时，模块 `__getattr__`
    经 bundle 惰性检查 JSON 的 mtime，文件一变就重载；
  - 因此抖音改版时只需编辑 `selectors_bundle.json`（或经 API reload），
    无需重启后端进程，即将到达的登录/定位/发送会自动用新选择器。

【重要——运行期取值】
热更新要求调用方在「使用瞬间」读取选择器，而不是导入时绑定一次。因此
`douyin_chat.py` 必须写成 `from . import selectors as _s` 然后 `_s.CHAT_EDITOR`。
若写成 `from .selectors import X`，import 时就会捕获旧值，热更新对该引用方失效。
`tests/fakes.py` 与 `backend/scripts/*` 仍用 `from .selectors import X`，它们只在
测试/诊断时比较默认值，属可接受（bundle 默认值恒等于这些常量的原值）。

注意：抖音页面结构随时可能变化，任何真实 E2E 前必须重新核对。
本项目不做任何浏览器指纹伪装。
"""

import hashlib
import json
from pathlib import Path

# bundle 文件：与 selectors.py 同目录，是运行时唯一可编辑来源。
_BUNDLE_PATH = Path(__file__).with_name("selectors_bundle.json")

# 内置默认值：当 bundle 文件缺失或某个字段缺失时兜底使用；
# 也是首次运行生成 selectors_bundle.json 的种子。
# key 命名与既有模块常量一致，便于 diff 与调参。
_DEFAULTS: dict[str, object] = {
    "CHAT_URL": "https://www.douyin.com/chat",
    # 会话列表出现 = 已登录（登录未完成时页面不会渲染会话列表）
    "CONVERSATION_LIST": ".conversationConversationListwrapper",
    "CONVERSATION_ITEM": ".conversationConversationItemwrapper",
    "CONVERSATION_TITLE": ".conversationConversationItemtitle",
    # 搜索结果行（裸 SearchPanelitem 会匹配外层包装容器，必须用 box 变体）
    "SEARCH_RESULT_ITEM": (
        'div[class*="SearchPanelitembox"], '
        'div[class*="SearchPanelitem-box"], '
        'div[class*="SearchPanelitem_box"]'
    ),
    # 搜索行内的名称节点（多套大小写/连字符变体）
    "SEARCH_NAME_SELECTORS": (
        '[class*="SearchPanelitemtitle"]',
        '[class*="SearchPanelitemTitle"]',
        '[class*="SearchPanelitem_title"]',
        '[class*="SearchPanelitem-title"]',
        '[class*="SearchPanelitemname"]',
        '[class*="SearchPanelitemName"]',
        '[class*="SearchPanelitem_name"]',
        '[class*="SearchPanelitem-name"]',
    ),
    # 搜索结果中只包含实际会话名的高亮节点；不要把未读数/动作文本并入名称。
    "SEARCH_NAME_EXACT_SELECTORS": (
        '[class*="SearchPanelitemsearch_highlight"]',
        '[class*="SearchPanelitemsearch-highlight"]',
        '[class*="SearchPanelitemsearchHighlight"]',
    ),
    # 搜索结果的「发消息」按钮
    "SEARCH_CHAT_BTN": 'div[class*="SearchPanelitemchat_btn"]',
    # 右侧会话面板标题，用于打开会话后的二次核对
    "RIGHT_TITLE": ".RightPanelHeadertitleContainer",
    # 顶部搜索框（多套变体，优先 semi-input）
    "SEARCH_INPUT": 'input.semi-input[placeholder="搜索"][type="text"]',
    "SEARCH_INPUTS": (
        'input.semi-input[placeholder="搜索"][type="text"]',
        'input[placeholder="搜索"]',
    ),
    # 消息编辑器
    "CHAT_EDITOR": 'div[data-slate-editor="true"][contenteditable="true"]',
    # 图片上传控件
    "IMAGE_INPUTS": (
        'input[type="file"][accept*="image"]',
        'input[type="file"]',
    ),
    # 图片/附件上传入口：点 .semi-upload-add 唤起文件选择器
    "IMAGE_UPLOAD_TRIGGER": ".semi-upload-add",
    # 上传确认弹窗里的「发送」按钮
    "IMAGE_DIALOG_SEND": (
        '.semi-modal button[class*="primary"]',
        'button[class*="semi-button-primary"]',
        '[class*="confirm"] button[class*="primary"]',
    ),
    # 发送失败/重试标记（裸 SendStatusicon 是多状态共用类，绝不能当失败条件）
    "SEND_FAILURE_MARKERS": (
        '[class*="sendFailed"]',
        '[class*="SendFailed"]',
        '[class*="ContentSideSendStatusretry"]',
        '[class*="SendStatusretry"]',
        '[aria-label*="重试"]',
        '[title*="重试"]',
    ),
    # 发送按钮：新版编辑器按 Enter 不发送，必须优先点发布按钮
    "SEND_BUTTONS": (
        '[class*="messageMsgInputpublishBtn"]',
        '.e2e-send-msg-bt',
        'button[aria-label*="发送"]',
        '[role="button"][aria-label*="发送"]',
    ),
    # 表情(sticker)发送：打开表情面板的按钮 / 表情面板容器 / 表情项及其名称节点
    # （概念与类名参考 unmev/douyin-auto-fire；真机 E2E 前必须核对并尽量走 bundle 热更）
    "STICKER_BUTTONS": (
        'svg.messageMsgInputiconAction',
        'button[aria-label*="表情"]',
        '[role="button"][aria-label*="表情"]',
        '[title*="表情"]',
    ),
    "STICKER_PANELS": (
        '.componentsemojiemojiPanel',
        '[class*="emojiPanel"]',
        '[role="dialog"]',
        '[class*="sticker"]',
    ),
    "STICKER_ITEM": '.emojiEmojiItememojiItem',
    "STICKER_ITEM_NAME": '.emojiEmojiItememojiItemDesc',
}


class _Bundle:
    """持有当前 selector 值集合，mtime 惰性重载的容器。

    读文件时机：`get()` 被调用时检查 JSON mtime，变了才重读；否则用内存缓存。
    文件缺失时用内置默认值生成一份（作为首次运行的种子），字段缺失同理。
    进程内单例（`_bundle`），事件循环单线程下无需加锁。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else _BUNDLE_PATH
        self._values: dict[str, object] = {}
        self._mtime: float | None = None
        self._reload(force=True)

    def _reload(self, *, force: bool = False) -> None:
        current_mtime = self._file_mtime()
        if not force and current_mtime == self._mtime:
            return
        values = dict(_DEFAULTS)
        if current_mtime is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key, value in data.items():
                        if value is None:
                            continue
                        values[key] = value
            except (json.JSONDecodeError, OSError):
                # 文件内容非法/不可读：保留默认值，避免半成功状态。
                pass
        else:
            # 文件不存在：写一份默认值作为种子，便于运维直接编辑。
            try:
                self.path.write_text(
                    json.dumps(_DEFAULTS, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                current_mtime = self._file_mtime()
            except OSError:
                pass
        self._values = values
        self._mtime = current_mtime

    def _file_mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def get(self, name: str):
        self._reload()
        if name not in self._values:
            raise AttributeError(name)
        return self._values[name]

    def reload(self, *, force: bool = True) -> None:
        self._reload(force=force)

    def as_dict(self) -> dict[str, object]:
        self._reload()
        return dict(self._values)

    def digest(self) -> str:
        """当前 bundle 内容摘要（前 12 位十六进制），用于诊断工件标识选择器版本。

        以 bundle 文件字节为准；文件缺失时退化为对内存值的规范化序列化取摘要。
        """
        self._reload()
        try:
            payload = self.path.read_bytes()
        except OSError:
            payload = json.dumps(self._values, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]


# 进程内单例
_bundle = _Bundle()


def current(name: str):
    """取当前 selector 值（公开入口，供 API / 诊断使用）。"""
    return _bundle.get(name)


def all_selectors() -> dict[str, object]:
    """返回当前 bundle 全量快照。"""
    return _bundle.as_dict()


def bundle_digest() -> str:
    """当前 selector bundle 的内容摘要（诊断工件用，标识选择器版本）。"""
    return _bundle.digest()


def reload_bundle(*, force: bool | None = None) -> dict[str, object]:
    """重新加载 bundle（默认强制重读文件），返回当前全量快照。"""
    _bundle.reload(force=True if force is None else force)
    return _bundle.as_dict()


def _reset_for_tests(path: Path | None = None) -> None:
    """测试专用：重建单例 bundle（可指向临时路径）。"""
    global _bundle
    _bundle = _Bundle(path)


def __getattr__(name: str):
    # 模块级动态属性：`selectors.CONVERSATION_LIST` 运行期取当前 bundle 值。
    # 仅对已知 selector 名生效；未知名抛 AttributeError，保持既有 import 语义。
    if name.startswith("__"):
        raise AttributeError(name)
    return _bundle.get(name)
