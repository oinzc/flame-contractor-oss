"""区分消息状态：困在编辑器 or 已进入气泡列表；并核对发送按钮选择器。

用法：
  .venv\\Scripts\\python.exe scripts\\check_state.py <account_id> <text_fragment>
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
from app.execution.selectors import SEND_BUTTONS


async def main() -> None:
    account_id, text = sys.argv[1], sys.argv[2]
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

    detail = await page.evaluate(
        """(text) => {
          const out = {};
          out.editor_texts = [...document.querySelectorAll(
            '[data-slate-editor="true"][contenteditable="true"]'
          )].map(e => (e.innerText || '').trim().slice(0, 200));
          const list = document.querySelector('.messageMessageListlist');
          out.has_message_list = !!list;
          const boxes = [];
          document.querySelectorAll('[class*="messageMessageBoxcontentBox"], [class*="messageMessageBoxmessageBox"]').forEach(el => {
            const cls = typeof el.className === 'string' ? el.className : '';
            boxes.push({ cls: cls.slice(0, 90), text: (el.innerText || '').trim().slice(0, 120) });
          });
          out.bubbles = boxes.slice(-10);
          const btn_report = [];
          const candidates = [
            '[class*="messageMsgInputpublishBtn"]',
            '.e2e-send-msg-bt',
            'button[aria-label*="发送"]',
            '[role="button"][aria-label*="发送"]',
          ];
          candidates.forEach(sel => {
            btn_report.push({
              sel,
              count: document.querySelectorAll(sel).length,
            });
          });
          // 找一切文本为「发送」的元素及其类名（不设上限地看前几个）
          const send_named = [];
          document.querySelectorAll('div,button,span').forEach(el => {
            if ((el.textContent || '').trim() === '发送') {
              send_named.push({
                tag: el.tagName,
                cls: (typeof el.className === 'string' ? el.className : '').slice(0, 120),
                editable_inside: !!el.closest('[contenteditable="true"]'),
              });
            }
          });
          out.send_named_elements = send_named.slice(0, 8);
          out.btn_candidates = btn_report;
          // 文本精确落点（叶子级）
          const leaves = [];
          document.querySelectorAll('div, span, p').forEach(el => {
            const direct = Array.from(el.childNodes || []).some(
              n => n.nodeType === 3 && (n.textContent || '').includes(text)
            );
            if (direct && leaves.length < 8) {
              leaves.push({
                tag: el.tagName,
                cls: (typeof el.className === 'string' ? el.className : '').slice(0, 140),
                in_editor: !!el.closest('[data-slate-editor="true"]'),
                in_msglist: !!el.closest('.messageMessageListlist'),
              });
            }
          });
          out.text_leaf_hits = leaves;
          return out;
        }""",
        text,
    )

    print(json.dumps({"opened": opened.opened, "title": opened.title, **detail},
                     ensure_ascii=False, indent=2))
    await context.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
