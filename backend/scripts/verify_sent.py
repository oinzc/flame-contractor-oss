"""验证消息是否真的发出：打开与小号的会话，全文检索目标文案。

用法：
  .venv\\Scripts\\python.exe scripts\\verify_sent.py <account_id> <message_text>
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright

from app.core.config import get_settings
from app.execution.douyin_chat import DouyinChat
from app.execution.profile_store import ensure_profile_dir


async def main() -> None:
    account_id, message_text = sys.argv[1], sys.argv[2]
    settings = get_settings()
    profile = ensure_profile_dir(settings, account_id)

    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
    )
    page = context.pages[0] if context.pages else await context.new_page()
    chat = DouyinChat(page)

    await page.goto("https://www.douyin.com/chat")
    await asyncio.sleep(7)
    try:
        await chat.warm_up_list()
    except Exception:
        pass
    opened = await chat.open_conversation("oinzc")
    await asyncio.sleep(3)

    report = {"opened": opened.opened, "title": opened.title}

    hit = await page.evaluate(
        """(text) => {
          const hits = [];
          document.querySelectorAll('div, span, p').forEach((el) => {
            const t = (el.textContent || '').trim();
            if (!t) return;
            const cls = typeof el.className === 'string' ? el.className : '';
            if (t.includes(text)) {
              if (!cls.includes('SearchPanel')) hits.push({ tag: el.tagName, cls });
            }
          });
          return {
            any_hit: hits.length > 0,
            hits: hits.slice(0, 6),
            buttons_with_send: Array.from(document.querySelectorAll('div,button'))
              .map((el) => (el.textContent || '').trim())
              .filter((t) => t === '发送').length,
          };
        }""",
        message_text,
    )
    report["message_check"] = hit

    print(json.dumps(report, ensure_ascii=False, indent=2))
    await context.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
