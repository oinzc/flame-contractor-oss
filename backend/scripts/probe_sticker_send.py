"""sticker 真机发送 E2E：直接调用 DouyinChat.send_sticker 发送「比心」。

说明：不走 BrowserManager.run_send，因为 run_send 入口用 headless 打开后立即
调用 is_logged_in()，存在「会话列表未渲染完即返回 False」的已知假阴性
（会导致 auth_expired，独立于图片/表情功能）。本脚本用与 probe_sticker 相同的
打开链路（sleep + warm_up + open_conversation）先稳打开会话，再真实发送表情并走
终态确认。只应在用户确认后运行（会给 oinzc 小号发一条表情）。

用法：
  .venv\\Scripts\\python.exe scripts\\probe_sticker_send.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.execution.douyin_chat import DouyinChat, Sticker  # noqa: E402
from app.execution.profile_store import ensure_profile_dir  # noqa: E402

ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"  # oinzc


async def main() -> None:
    settings = get_settings()
    profile = ensure_profile_dir(settings, ACCOUNT_ID)

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

        sticker = Sticker(name="比心", label="比心")
        try:
            await chat.send_sticker(sticker)
            print("SEND_STICKER OK, last_bubble_seen:", chat.last_bubble_seen)
        except Exception as exc:
            print("SEND_STICKER_FAILED:", type(exc).__name__, exc)

        confirmed = await chat.image_bubble_confirmed()
        print("IMAGE_BUBBLE_CONFIRMED:", confirmed)
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
