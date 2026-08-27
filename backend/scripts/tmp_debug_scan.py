import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from app.execution.douyin_chat import DouyinChat
from tests.fakes import FakePage


async def main() -> None:
    page = FakePage(
        logged_in=True,
        conversation_titles=("好友A", "好友A同学"),
        right_title="好友A",
    )
    chat = DouyinChat(page)
    opened = await chat.open_conversation("好友A")
    print("RESULT:", opened)
    print("search_active:", page.search_active)
    print("titles_now:", page.conversation_titles)
    print("fills:", page.fills)
    print("clicked:", page.clicked)


asyncio.run(main())
