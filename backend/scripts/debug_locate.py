"""调试脚本：用真实 Profile 逐步执行定位流程并打印每一步的观察结果。"""

import asyncio
import json
import sys

sys.path.insert(0, ".")

from playwright.async_api import async_playwright  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.execution.douyin_chat import DouyinChat  # noqa: E402
from app.execution.selectors import SEARCH_INPUTS, SEARCH_RESULT_ITEM  # noqa: E402

ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"


async def main() -> None:
    settings = get_settings()
    profile = settings.profiles_dir / ACCOUNT_ID
    print("PROFILE:", profile, "exists:", profile.exists())

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=True
        )
        page = context.pages[0] if context.pages else await context.new_page()
        chat = DouyinChat(page)

        await chat.open_chat_page()
        print("LOGGED_IN:", await chat.is_logged_in())

        await chat.warm_up_list()
        print("WARM_UP_DONE, visible:", await chat.visible_titles(20))

        # 手动逐步搜索
        await chat._fill_first(SEARCH_INPUTS, "oinzc")
        print("FILLED_SEARCH")
        for wait in range(1, 9):
            await asyncio.sleep(1)
            boxes = await page.query_selector_all(SEARCH_RESULT_ITEM)
            print(f"t={wait}s boxes={len(boxes)}")
            if boxes:
                for index, box in enumerate(boxes[:5]):
                    try:
                        text = await box.inner_text()
                        print(f"  box[{index}] text={text!r}")
                    except Exception as exc:
                        print(f"  box[{index}] inner_text error: {type(exc).__name__}")
                break

        # 用系统自身的扫描逻辑
        result = await chat._scan_items("oinzc")
        print("SCAN_RESULT:", result)
        if not result.opened:
            clicked = await chat._try_click_exact_by_text("oinzc")
            print("TEXT_FALLBACK_CLICKED:", clicked)
            if clicked:
                confirmed = await chat._confirm_opened("oinzc")
                print("CONFIRMED:", confirmed)

        # 诊断 + 首个 box 的原始 HTML
        diag = await chat.collect_search_diagnostics("oinzc")
        print("DIAGNOSTICS:", json.dumps(diag, ensure_ascii=False, indent=2))
        boxes = await page.query_selector_all(SEARCH_RESULT_ITEM)
        if boxes:
            html = await boxes[0].evaluate("(el) => el.outerHTML.slice(0, 2500)")
            print("FIRST_BOX_HTML:", html)

        await context.close()


asyncio.run(main())
