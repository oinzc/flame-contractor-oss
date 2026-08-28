"""sticker DOM 真机探针：只定位、绝不发送。

打开指定账号（默认 oinzc）的会话页 → 打开其好友会话 → 点表情按钮 → 采集
表情面板 DOM 诊断，核对 STICKER_BUTTONS/STICKER_PANELS/表情项/名称节点的
类名是否与 selectors_bundle.json 一致。

安全：只点「打开面板」按钮与面板内分类标签；绝不点击表情项本身（那会发送）。

用法：
  .venv\\Scripts\\python.exe scripts\\probe_sticker.py [account_id]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.execution import selectors as _s  # noqa: E402
from app.execution.douyin_chat import DouyinChat  # noqa: E402
from app.execution.profile_store import ensure_profile_dir  # noqa: E402

DEFAULT_ACCOUNT = "00000000-0000-0000-0000-000000000000"  # oinzc


async def main() -> None:
    account_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ACCOUNT
    settings = get_settings()
    profile = ensure_profile_dir(settings, account_id)

    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
    )
    page = context.pages[0] if context.pages else await context.new_page()
    chat = DouyinChat(page)

    try:
        await page.goto("https://www.douyin.com/chat")
        await asyncio.sleep(6)
        try:
            await chat.warm_up_list()
        except Exception:
            pass
        opened = await chat.open_conversation("oinzc")
        print("OPENED:", opened)

        # 1) 表情按钮各候选的可见情况
        btn_report = []
        for selector in _s.STICKER_BUTTONS:
            try:
                loc = page.locator(selector).first
                btn_report.append({
                    "selector": selector,
                    "count": await loc.count(),
                    "visible": await loc.is_visible() if await loc.count() else False,
                })
            except Exception as exc:
                btn_report.append({"selector": selector, "error": type(exc).__name__})
        print("STICKER_BUTTONS:", json.dumps(btn_report, ensure_ascii=False))

        # 2) 找第一个可见按钮并点开面板
        button = await chat._first_visible_locator(_s.STICKER_BUTTONS)
        if button is None:
            print("NO_VISIBLE_STICKER_BUTTON")
            return
        await button.click(force=True)
        await asyncio.sleep(1.5)

        # 3) 面板各候选
        panel_report = []
        for selector in _s.STICKER_PANELS:
            try:
                loc = page.locator(selector).first
                panel_report.append({
                    "selector": selector,
                    "count": await loc.count(),
                    "visible": await loc.is_visible() if await loc.count() else False,
                })
            except Exception as exc:
                panel_report.append({"selector": selector, "error": type(exc).__name__})
        print("STICKER_PANELS:", json.dumps(panel_report, ensure_ascii=False))

        panel = await chat._first_visible_locator(_s.STICKER_PANELS)
        if panel is None:
            print("NO_VISIBLE_STICKER_PANEL")
            return

        # 4) 表情项 + 名称节点
        item_count = 0
        names = []
        try:
            items = panel.locator(_s.STICKER_ITEM)
            item_count = await items.count()
            cap = min(item_count, 30)
            for index in range(cap):
                try:
                    desc = items.nth(index).locator(_s.STICKER_ITEM_NAME)
                    if await desc.count():
                        t = (await desc.first.inner_text()).strip()
                        if t:
                            names.append(t)
                except Exception:
                    continue
        except Exception as exc:
            print("ITEM_SCAN_ERROR:", type(exc).__name__)
        print("STICKER_ITEM_COUNT:", item_count)
        print("STICKER_ITEM_NAMES:", json.dumps(names, ensure_ascii=False))

        # 5) 面板内所有可见元素的类名样本（低频，帮手工核对类名）
        sample = await page.evaluate(
            """() => {
              const seen = new Set();
              const out = [];
              document.querySelectorAll('div, span, img, svg, button').forEach(el => {
                const cls = (typeof el.className === 'string' ? el.className : '').trim();
                if (!cls) return;
                const key = cls.slice(0, 60);
                if (seen.has(key)) return;
                seen.add(key);
                out.push({ tag: el.tagName, cls: cls.slice(0, 80) });
              });
              return out.slice(0, 40);
            }"""
        )
        print("PANEL_CLASS_SAMPLE:", json.dumps(sample, ensure_ascii=False))
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
