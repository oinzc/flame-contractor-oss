# -*- coding: utf-8 -*-
"""以有头模式打开抖音聊天页（项目登录态），供维护者人工指认续火对象。

用法：先设置 FLAME_ACCOUNT_ID 为账号在数据库里的 id（账号卡片 URL 或接口里可见），
再在容器/本机执行本脚本。独立进程运行，窗口保持 40 分钟后自动关闭。
"""

import asyncio
import os
import sys

ACCOUNT_ID = os.environ.get("FLAME_ACCOUNT_ID", "").strip()


async def main():
    if not ACCOUNT_ID:
        sys.exit("请先设置 FLAME_ACCOUNT_ID 环境变量，值为目标账号在数据库中的 id。")

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
