"""headless 登录检测修复的真机验证：对比单次 is_logged_in vs wait_logged_in。

只检测、绝不发送。打开 oinzc 持久 profile（headless）→ 打开聊天页（短暂等待，
模拟页面加载早期会话列表未渲染完的场景）→ 记录单次 is_logged_in 结果与耗时 →
再跑 wait_logged_in(timeout=8) 记录最终结果与耗时 → 截图保存，供视觉桥核对
会话列表是否渲染。

用法：
  .venv\\Scripts\\python.exe scripts\\probe_login_wait.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.execution.douyin_chat import DouyinChat  # noqa: E402
from app.execution.profile_store import ensure_profile_dir  # noqa: E402

ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"  # oinzc
SHOT = Path(__file__).resolve().parents[1] / "data" / "login_probe.png"


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
        # 短暂等待后立刻单次查询（模拟页面加载早期 / run_send 曾用的行为）
        await asyncio.sleep(1.0)
        t0 = time.monotonic()
        single = await chat.is_logged_in()
        single_ms = (time.monotonic() - t0) * 1000

        t0 = time.monotonic()
        waited = await chat.wait_logged_in(timeout=8.0, poll_interval=0.3)
        waited_ms = (time.monotonic() - t0) * 1000

        SHOT.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(SHOT), full_page=False)

        print("IS_LOGGED_IN(single):", single, f"({single_ms:.0f}ms)")
        print("WAIT_LOGGED_IN(poll):", waited, f"({waited_ms:.0f}ms)")
        print("SCREENSHOT:", SHOT)
        print("FIX_VERDICT:",
              "FIXED" if (waited and not single) else
              ("OK(single already True)" if (single and waited) else "STILL_BROKEN"))
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
