"""为 README 截取演示控制台的界面图。

先启动演示实例（scripts/demo_server.py），再运行本脚本：
  .venv/Scripts/python.exe scripts/demo_screenshots.py
输出到 docs/assets/screenshots/。截图固定使用 Asia/Shanghai 时区与 1440×900 视口，
以保证重复生成时画面一致。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import Page, async_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "docs" / "assets" / "screenshots"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def nav(page: Page, label: str) -> None:
    await page.get_by_role("button", name=label, exact=True).click()
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(600)


async def shot(page: Page, name: str, **kwargs) -> None:
    path = OUT_DIR / f"{name}.png"
    await page.screenshot(path=str(path), **kwargs)
    print(f"已保存 {path.relative_to(REPO_ROOT)}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            timezone_id="Asia/Shanghai",
            locale="zh-CN",
            color_scheme="dark",
            reduced_motion="reduce",
        )
        page = await context.new_page()
        await page.goto(args.base_url)
        await page.get_by_placeholder("输入用户名").fill("demo")
        await page.get_by_placeholder("输入密码").fill("demo-password")
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1200)
        await shot(page, "dashboard")

        await nav(page, "账号")
        await shot(page, "accounts")
        await page.get_by_role("button", name="立即发送").first.click()
        await page.wait_for_timeout(800)
        await shot(page, "send-confirm")
        await page.get_by_role("button", name="取消").click()
        await page.wait_for_timeout(300)

        await nav(page, "好友")
        await shot(page, "targets")
        await page.locator("#scan-tab").click()
        await page.wait_for_timeout(800)
        await shot(page, "scan-reconciliation")

        await nav(page, "任务")
        await shot(page, "jobs")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
