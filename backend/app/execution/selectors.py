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

# 搜索结果行（来自 unmev 的经验：不能用裸 SearchPanelitem，它会匹配外层
# SearchPanelitems 包装容器导致取错行；必须用 box 变体）
SEARCH_RESULT_ITEM = (
    'div[class*="SearchPanelitembox"], '
    'div[class*="SearchPanelitem-box"], '
    'div[class*="SearchPanelitem_box"]'
)

# 搜索行内的名称节点（多套大小写/连字符变体，同 unmev）
SEARCH_NAME_SELECTORS = (
    '[class*="SearchPanelitemtitle"]',
    '[class*="SearchPanelitemTitle"]',
    '[class*="SearchPanelitem_title"]',
    '[class*="SearchPanelitem-title"]',
    '[class*="SearchPanelitemname"]',
    '[class*="SearchPanelitemName"]',
    '[class*="SearchPanelitem_name"]',
    '[class*="SearchPanelitem-name"]',
)

# 搜索结果的「发消息」按钮（截图已证实其存在；概念来自 ScriptCat 的
# SearchPanelitemchat_btn。没有历史会话的好友必须点它发起聊天）
SEARCH_CHAT_BTN = 'div[class*="SearchPanelitemchat_btn"]'

# 右侧会话面板标题，用于打开会话后的二次核对
RIGHT_TITLE = ".RightPanelHeadertitleContainer"

# 顶部搜索框（chat 页面；多套变体，优先 semi-input）
SEARCH_INPUT = 'input.semi-input[placeholder="搜索"][type="text"]'
SEARCH_INPUTS = (
    'input.semi-input[placeholder="搜索"][type="text"]',
    'input[placeholder="搜索"]',
)

# 消息编辑器（M3 发送使用）
CHAT_EDITOR = 'div[data-slate-editor="true"][contenteditable="true"]'

# 发送失败/重试标记（概念参考 unmev/douyin-auto-fire 的 SendStatusretry 类标记；
# 类名随页面版本变化，真实 E2E 前必须核对，并保持“检测不到就按结果未知处理”）
SEND_FAILURE_MARKERS = (
    '[class*="SendStatusretry"]',
    '[class*="SendStatusfailed"]',
    '[class*="send-status-retry"]',
)
