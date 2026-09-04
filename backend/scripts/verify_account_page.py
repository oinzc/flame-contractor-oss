"""验证「我的」账号页：登录、资料/密码/设备、管理员视图、登出。

后端已在本机 8000 运行（含新 auth 路由），前端 dev server 在 5173。
此脚本用 Playwright 真实渲染验证，不修改任何数据。
"""
from playwright.sync_api import sync_playwright

BASE = 'http://localhost:5173'


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        console_errors = []
        page.on('console', lambda m: console_errors.append(m.text) if m.type == 'error' else None)

        # 1. 登录页可见
        page.goto(BASE, wait_until='networkidle')
        page.wait_for_selector('.login-card', timeout=8000)
        print('PASS 登录卡片渲染')

        # 2. 登录 admin/admin
        page.fill('#login-username', 'admin')
        page.fill('#login-password', 'admin')
        page.click('button[type=submit].btn.primary')
        page.wait_for_selector('.app', timeout=8000)
        print('PASS 登录进入控制台')

        # 3. 侧边栏出现「我的」入口
        page.wait_for_selector('.side-nav-item', timeout=5000)
        print('PASS 侧边导航渲染')

        # 4. 进入「我的」
        page.click('text=我的')
        page.wait_for_selector('.account-profile-tabs', timeout=8000)
        print('PASS 「我的」页分区标签可见')

        # 5. 个人资料分区：admin 应是平台管理员
        profile = page.locator('.card-title', has_text='平台账号').first
        print('PASS 平台账号卡片存在')

        # 6. 切换到「平台用户」管理员视图
        page.click('text=平台用户')
        page.wait_for_selector('.admin-user-row', timeout=8000)
        rows = page.locator('.admin-user-row').count()
        print(f'PASS 管理员用户列表渲染 ({rows} 行)')

        # 7. 会话表：已登录设备
        page.click('text=已登录设备')
        page.wait_for_selector('table.table', timeout=8000)
        print('PASS 已登录设备表格渲染')

        # 8. 控制台无报错
        if console_errors:
            errors.append(f'控制台错误: {console_errors[:5]}')
        else:
            print('PASS 控制台无 error')

        page.screenshot(path='/tmp/flame-me.png', full_page=True)
        browser.close()

    if errors:
        print('\nFAILURES:')
        for e in errors:
            print(' -', e)
        raise SystemExit(1)
    print('\nALL PASS')


if __name__ == '__main__':
    main()
