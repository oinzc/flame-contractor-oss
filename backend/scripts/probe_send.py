"""用产品代码路径对真实账号执行一次发送（会真的发出消息）。

用法：
  .venv\\Scripts\\python.exe scripts\\probe_send.py <account_id>
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
from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.execution.browser_manager import BrowserManager


async def main() -> None:
    account_id = sys.argv[1]
    settings = get_settings()
    run_migrations(settings.database_url)
    engine = build_engine(settings.database_url)

    pw = await async_playwright().start()
    manager = BrowserManager(engine, settings)
    manager._playwright = pw

    result = await manager.run_send(account_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    await manager.aclose()


if __name__ == "__main__":
    asyncio.run(main())
