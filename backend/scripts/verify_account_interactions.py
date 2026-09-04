"""验证「我的」页完整交互流——改密码、登出再登录、设备列表。

使用**临时注册用户**（系统随机名字，测完即弃），绝不触碰 admin 账号，
避免破坏真实运行环境。后端需在 8000、前端 dev server 在 5173。
"""
import uuid
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:5173'
USERNAME = f'it_user_{uuid.uuid4().hex[:6]}'
PASSWORD = 'it-pw-123456'
NEW_PASSWORD = 'it-new-pw-654321'


def do_login(page, username, password):
    page.goto(BASE, wait_until='networkidle')
    page.wait_for_selector('.login-card', timeout=8000)
    page.fill('#login-username', username)
    page.fill('#login-password', password)
    page.click('button[type=submit].btn.primary')
    page.wait_for_selector('.app', timeout=8000)


def main():
    steps = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # 1. 注册临时用户（进控制台）
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.goto(BASE, wait_until='networkidle')
        page.wait_for_selector('.login-card', timeout=8000)
        page.click('.login-tab:has-text("注册")')
        page.fill('#login-username', USERNAME)
        page.fill('#login-password', PASSWORD)
        page.click('button[type=submit].btn.primary')
        page.wait_for_selector('.app', timeout=8000)
        steps.append('PASS 注册临时用户进入控制台')

        # 2. 进入「我的」→ 改密码
        page.click('.side-nav-item:has-text("我的")')
        page.wait_for_selector('.account-profile-tabs', timeout=8000)
        page.click('.account-profile-tab:has-text("修改密码")')
        page.fill('#pw-current', PASSWORD)
        page.fill('#pw-new', NEW_PASSWORD)
        page.fill('#pw-new2', NEW_PASSWORD)
        page.click('button[type=submit]:has-text("修改密码")')
        # 等待成功提示
        page.wait_for_selector('text=密码已更新', timeout=8000)
        steps.append('PASS 前端改密码成功提示')

        # 3. 设备列表应至少有一条会话
        page.click('.account-profile-tab:has-text("已登录设备")')
        page.wait_for_selector('table.table', timeout=8000)
        rows = page.locator('table.table tbody tr').count()
        assert rows >= 1, f'设备列表为空 rows={rows}'
        steps.append(f'PASS 设备列表渲染（{rows} 条）')

        # 4. 登出 → 回到登录页
        page.click('button:has-text("退出登录")')
        page.wait_for_selector('.login-card', timeout=8000)
        steps.append('PASS 登出返回登录页')

        # 5. 用新密码登录成功
        do_login(page, USERNAME, NEW_PASSWORD)
        page.wait_for_selector('.app', timeout=8000)
        steps.append('PASS 新密码登录成功（改密已生效）')

        # 6. 用旧密码登录应失败（改密后旧密码失效）
        page2 = browser.new_page(viewport={'width': 1440, 'height': 900})
        page2.goto(BASE, wait_until='networkidle')
        page2.wait_for_selector('.login-card', timeout=8000)
        page2.fill('#login-username', USERNAME)
        page2.fill('#login-password', PASSWORD)
        page2.click('button[type=submit].btn.primary')
        page2.wait_for_selector('text=用户名或密码错误', timeout=8000)
        steps.append('PASS 旧密码登录被拒绝')

        browser.close()

    for s in steps:
        print(s)
    print('\nALL INTERACTIONS PASS')


if __name__ == '__main__':
    main()
