"""搜索定位实机诊断脚本。

用法：
  .venv\\Scripts\\python.exe scripts\\search_debug.py <account_id> [nickname]

行为：用该账号的持久 Profile 打开聊天页，滚动预热、搜索昵称、
采集各候选选择器的命中数与行内结构，并尝试点击「发消息」。
只读 + 打开会话，绝不发送消息。结果写入 artifacts/<account>/debug-search.json。
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

from app.core.config import get_settings
from app.execution.profile_store import ensure_profile_dir

CHAT_URL = "https://www.douyin.com/chat"
LIST_SELECTOR = ".conversationConversationListwrapper"
ROW_CANDIDATES = {
    "box": 'div[class*="SearchPanelitembox"]',
    "dash": 'div[class*="SearchPanelitem-box"]',
    "under": 'div[class*="SearchPanelitem_box"]',
    "bare_item": 'div[class*="SearchPanelitem"]',
    "bare_plural": 'div[class*="SearchPanelitems"]',
}
CHAT_BTN = 'div[class*="SearchPanelitemchat_btn"]'
TITLE_CHILD = '[class*="SearchPanelitemtitle"]'
RIGHT_TITLE = ".RightPanelHeadertitleContainer"
EDITOR = 'div[data-slate-editor="true"][contenteditable="true"]'


async def count_rows(page, selector: str) -> int:
    try:
        return len(await page.query_selector_all(selector))
    except Exception as exc:
        return f"ERR:{exc}"


async def main() -> None:
    account_id = sys.argv[1]
    nickname = sys.argv[2] if len(sys.argv) > 2 else "oinzc"
    settings = get_settings()
    profile = ensure_profile_dir(settings, account_id)

    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
    )
    page = context.pages[0] if context.pages else await context.new_page()
    report: dict = {"nickname": nickname}

    async def snapshot(tag: str) -> None:
        data = {
            tag: {
                "input_candidates": {
                    sel: await count_rows(page, sel)
                    for sel in (
                        'input.semi-input[placeholder="搜索"][type="text"]',
                        'input[placeholder="搜索"]',
                    )
                },
                "row_counts": {name: await count_rows(page, sel) for name, sel in ROW_CANDIDATES.items()},
                "chat_btn_count": await count_rows(page, CHAT_BTN),
                "right_title_texts": [
                    (await el.inner_text()).strip()
                    for el in await page.query_selector_all(RIGHT_TITLE)
                ],
                "editor_present": await page.query_selector(EDITOR) is not None,
            }
        }
        report.update(data)
        print(json.dumps(data, ensure_ascii=False, indent=2))

    await page.goto(CHAT_URL)
    await asyncio.sleep(7)
    report["initial_list_items"] = await count_rows(page, LIST_SELECTOR)

    # 预热
    list_el = await page.query_selector(LIST_SELECTOR)
    for _ in range(6):
        if list_el is None:
            break
        try:
            await page.evaluate("(el) => { el.scrollTop += 900 }", list_el)
        except Exception:
            break
        await asyncio.sleep(0.9)
    report["after_warmup_scroll_list_items"] = await count_rows(page, LIST_SELECTOR)
    await snapshot("state_warm")

    # 搜索
    inbox = await page.query_selector('input[placeholder="搜索"]')
    if inbox is None:
        report["search_input"] = "NOT FOUND"
    else:
        await inbox.click()
        await inbox.fill(nickname)
        # 分阶段观察结果渲染
        for delay in (3, 6, 10):
            await asyncio.sleep(delay / 2)
            await snapshot(f"state_after_{delay}s_total")
            boxes = await page.query_selector_all(ROW_CANDIDATES["box"])
            if boxes:
                break

    rows = await page.query_selector_all(ROW_CANDIDATES["box"]) or await page.query_selector_all(
        ROW_CANDIDATES["bare_item"]
    )
    report["rows_dump"] = []
    for row in rows[:6]:
        entry = {"inner_text_first200": ((await row.inner_text()) or "")[:200]}
        title_node = await row.query_selector(TITLE_CHILD)
        entry["title_text"] = (await title_node.inner_text()).strip() if title_node else None
        btn = await row.query_selector(CHAT_BTN)
        entry["has_chat_btn"] = btn is not None
        html = await row.evaluate("el => el.outerHTML")
        entry["outer_head"] = html[:500]
        report["rows_dump"].append(entry)

    # 点击第一个「发消息」（若目标行匹配则更好；此处仅验证点击链路）
    if rows:
        btn = await rows[0].query_selector(CHAT_BTN)
        click_target = btn or rows[0]
        try:
            await click_target.click()
            await asyncio.sleep(3)
            report["after_click_right_title_texts"] = [
                (await el.inner_text()).strip()
                for el in await page.query_selector_all(RIGHT_TITLE)
            ]
            report["after_click_editor_present"] = await page.query_selector(EDITOR) is not None
        except Exception as exc:
            report["click_error"] = repr(exc)

    out_dir = settings.artifacts_dir / account_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "debug-search.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nREPORT_SAVED: {out_path}")

    await context.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
