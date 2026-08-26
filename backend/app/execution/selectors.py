"""抖音网页版 DOM 适配层。

选择器概念参考了 MIT 许可的开源项目（unmev/douyin-auto-fire、
dr-190/ScriptCat-Douyin-Fire-Helper、2061360308/DouYinSparkFlow），
按本项目的安全边界重写并集中于此，便于未来按页面版本热更新。

注意：抖音页面结构随时可能变化，任何真实 E2E 前必须重新核对。
本项目不做任何浏览器指纹伪装。
"""

CHAT_URL = "https://www.douyin.com/chat"

# 会话列表出现 = 已登录（登录未完成时页面不会渲染会话列表）
CONVERSATION_LIST = ".conversationConversationListwrapper"
CONVERSATION_ITEM = ".conversationConversationItemwrapper"
CONVERSATION_TITLE = ".conversationConversationItemtitle"

# 右侧会话面板标题，用于打开会话后的二次核对
RIGHT_TITLE = ".RightPanelHeadertitleContainer"

# 顶部搜索框（chat 页面）
SEARCH_INPUT = 'input.semi-input[placeholder="搜索"][type="text"]'

# 消息编辑器（M3 发送使用）
CHAT_EDITOR = 'div[data-slate-editor="true"][contenteditable="true"]'

# 发送失败/重试标记（概念参考 unmev/douyin-auto-fire 的 SendStatusretry 类标记；
# 类名随页面版本变化，真实 E2E 前必须核对，并保持“检测不到就按结果未知处理”）
SEND_FAILURE_MARKERS = (
    '[class*="SendStatusretry"]',
    '[class*="SendStatusfailed"]',
    '[class*="send-status-retry"]',
)
