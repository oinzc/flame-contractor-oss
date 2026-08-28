"""selector bundle 热更新：默认加载 / 显式 reload / 惰性 mtime / 运行期取值 / API。

这些测试会构造临时 bundle 文件并替换进程内单例（_reset_for_tests），
每个测试结束后由 autouse fixture 恢复为默认（真实 selectors_bundle.json），
避免污染其它测试与仓库里的 JSON。
"""

import asyncio
import json
import os

import pytest

from app.execution import selectors as sel
from app.execution.douyin_chat import DouyinChat
from app.execution.selectors import _BUNDLE_PATH


@pytest.fixture(autouse=True)
def _restore_bundle():
    yield
    sel._reset_for_tests()  # 恢复为默认（指向真实 selectors_bundle.json）


def _default_dict() -> dict:
    return json.loads(_BUNDLE_PATH.read_text(encoding="utf-8"))


def _write_bundle(path, data: dict, *, advance_mtime: bool = False) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if advance_mtime:
        st = path.stat()
        os.utime(path, (st.st_atime + 2, st.st_mtime + 2))


# ---------------------------------------------------------------- 默认加载


def test_bundle_loads_defaults():
    current = sel.all_selectors()
    assert current["CHAT_EDITOR"] == 'div[data-slate-editor="true"][contenteditable="true"]'
    assert current["CONVERSATION_LIST"] == ".conversationConversationListwrapper"
    assert current["RIGHT_TITLE"] == ".RightPanelHeadertitleContainer"
    assert current["IMAGE_UPLOAD_TRIGGER"] == ".semi-upload-add"
    assert len(current["SEND_BUTTONS"]) >= 1
    assert len(current["SEARCH_NAME_SELECTORS"]) >= 1
    # 与真实 bundle 文件一致
    assert current == _default_dict()


def test_unknown_selector_raises_attribute_error():
    with pytest.raises(AttributeError):
        sel.current("THIS_IS_NOT_A_SELECTOR")


# ---------------------------------------------------------------- 显式 reload


def test_reload_reads_updated_file(tmp_path):
    target = tmp_path / "bundle.json"
    changed = _default_dict()
    changed["CHAT_EDITOR"] = "div[data-slate-editor='changed'][contenteditable='true']"
    _write_bundle(target, changed)
    sel._reset_for_tests(target)

    assert sel.current("CHAT_EDITOR") == changed["CHAT_EDITOR"]
    assert sel.current("CONVERSATION_LIST") == _default_dict()["CONVERSATION_LIST"]


# ---------------------------------------------------------------- 惰性 mtime


def test_lazy_mtime_reload_without_explicit_call(tmp_path):
    target = tmp_path / "bundle.json"
    base = _default_dict()
    _write_bundle(target, base)
    sel._reset_for_tests(target)

    assert sel.current("CHAT_EDITOR") == base["CHAT_EDITOR"]

    changed = dict(base)
    changed["CHAT_EDITOR"] = ".someNewEditorSelector"
    _write_bundle(target, changed, advance_mtime=True)

    # 未调用 reload_bundle，仅靠下一次访问的 mtime 检查即拿到新值
    assert sel.current("CHAT_EDITOR") == ".someNewEditorSelector"


# ---------------------------------------------------------------- 运行期取值


class _SelectorProbePage:
    """按「期望的 selector 字符串」返回命中与否；仅暴露 query_selector。"""

    def __init__(self, wanted):
        self.wanted = wanted

    async def query_selector(self, selector):
        return object() if selector == self.wanted else None


def test_runtime_accessor_reflects_reload(tmp_path):
    """DouyinChat 用 _s.CONVERSATION_LIST 运行期取值，bundle 改了立即生效。"""
    target = tmp_path / "bundle.json"
    base = _default_dict()
    _write_bundle(target, base)
    sel._reset_for_tests(target)

    new_list = ".myCustomConversationList"
    page = _SelectorProbePage(wanted=new_list)
    chat = DouyinChat(page, sleep=lambda _s: asyncio.sleep(0))

    # 默认 CONVERSATION_LIST 不是 .myCustomConversationList → 未登录
    assert asyncio.run(chat.is_logged_in()) is False

    changed = dict(base)
    changed["CONVERSATION_LIST"] = new_list
    _write_bundle(target, changed, advance_mtime=True)

    # bundle 更新后，同一 chat 实例读到新选择器 → 已登录
    assert asyncio.run(chat.is_logged_in()) is True


# ---------------------------------------------------------------- API


def test_api_read_current_bundle(client):
    response = client.get("/api/selectors")
    assert response.status_code == 200
    body = response.json()
    assert "selectors" in body
    assert "count" in body
    assert "file_path" in body
    assert body["selectors"]["CHAT_EDITOR"] == 'div[data-slate-editor="true"][contenteditable="true"]'
    assert body["count"] == len(body["selectors"])


def test_api_reload_reloads_bundle(client):
    response = client.post("/api/selectors/reload")
    assert response.status_code == 200
    body = response.json()
    assert body["selectors"]["CONVERSATION_LIST"] == ".conversationConversationListwrapper"
    assert body["selectors"]["SEND_BUTTONS"]
