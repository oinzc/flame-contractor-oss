# -*- coding: utf-8 -*-
"""以有头模式打开抖音聊天页（项目登录态），供老大人工指认续火对象。
独立进程运行，保持窗口 40 分钟后自动关闭；也可手动结束进程。"""

import asyncio

ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"  # 示例账号


async def main():
    from app.core.config import get_settings
    from app.core.database import build_engine
    from app.execution.browser_manager import BrowserManager
    from app.execution.profile_store import ensure_profile_dir
    from app.execution.douyin_chat import DouyinChat

    settings = get_settings()
    engine = build_engine(settings.database_url)
    manager = BrowserManager(engine, settings)
    profile = ensure_profile_dir(settings, ACCOUNT_ID)
    context, page = await manager._open(profile, headless=False)
    chat = DouyinChat(page)
    await chat.open_chat_page()
    ok = await chat.wait_logged_in(timeout=30, poll_interval=1)
    print("logged_in:", ok, flush=True)
    print("WINDOW_READY", flush=True)
    await asyncio.sleep(40 * 60)


asyncio.run(main())
