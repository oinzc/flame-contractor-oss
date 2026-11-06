# 参与贡献

感谢你愿意参与。这个项目面向「单人、少量好友、低频发送」的本机自托管场景，改动请保持这个边界。

## 开始之前

- 开 issue 说明问题或想法，再开 PR，避免大改后方向不一致。
- 安全问题不要开公开 issue，见 [SECURITY.md](SECURITY.md)。
- 不要在提交里放真实账号信息：好友昵称、`platform_user_id`、Bark key、`.env`、数据库、浏览器 Profile、诊断截图都不行。测试请用虚构数据或 `tests/fakes.py`。

## 开发环境

```bash
# 后端（Python 3.12）
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest -q

# 前端（Node 22）
cd frontend
npm ci
npm run build          # tsc --noEmit && vite build
npm run dev            # 开发时使用
```

Windows 下把 `.venv/Scripts/python.exe` 换成 `.venv/bin/python`（Linux/macOS）。测试不需要真实浏览器，浏览器只在运行应用时才用。

## 提交信息

用 Conventional Commits 前缀，中文描述即可：

```text
fix(send): 扫描卡死不再阻塞发送
feat(accounts): 支持按备注过滤
docs: 补充 Docker 备份步骤
```

一个提交只做一件事；代码与测试放在同一个提交里。

## Pull Request

- 补齐或更新对应测试，`pytest -q` 全绿；前端改动跑一遍 `npm run build`。
- 涉及数据结构变更时，附 Alembic 迁移，并说明回滚方式。
- 涉及发送链路时，说明「结果无法确认」时的行为，必须保持不自动重发。
- 描述里写清：改了什么、为什么、怎么验证。
- PR 模板里勾选自查项；CI 通过后再请求 review。

## 代码风格

- 后端：Python 3.12 类型标注，尽量函数短小；业务错误走 `app.core.errors`。
- 前端：TypeScript 严格模式，组件就近放，请求集中在 API 层。
- 不引入难以维护的全局状态，不新增需要联网的服务依赖。

## 行为准则

参与即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
