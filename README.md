# 焰之契约者 · Flame Contractor

> 你与它立下契约，每日以一言续之，焰不可熄。

本机优先的多账号抖音续火控制台。当前进度：**M2** —— 控制平面、SQLite 数据模型、扫码登录流程与 Dry Run 已就绪；调度器与真实发送（M3/M4）尚未接入。

## 目录结构

```text
backend/    FastAPI + SQLModel + SQLite + Playwright + Alembic
frontend/   React + TypeScript + Vite 本地控制台
docs/       PRD、技术设计、API 文档、数据库 Schema
```

## 启动

### 1. 后端

```powershell
cd backend
python -m venv .venv            # 首次
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # 首次
.\.venv\Scripts\python.exe -m playwright install chromium          # 首次
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动自动执行 Alembic 迁移并创建 `data/flame-contractor.db`。

- 健康检查：<http://127.0.0.1:8000/health>
- OpenAPI 文档：<http://127.0.0.1:8000/docs>

### 2. 前端

```powershell
cd frontend
npm install          # 首次
npm run dev
```

打开 <http://localhost:5173>（开发服务器已代理 `/api` 与 `/health` 到后端）。

## 扫码登录与 Dry Run（M2）

1. 在控制台新建账号并添加契约对象；
2. 点击账号卡片上的「扫码登录」：本机弹出该账号专属浏览器窗口，用抖音 App 扫码；
3. 扫码成功后账号变为「健康」，再点「Dry Run」验证每个好友能否被严格定位；
4. Dry Run 只定位、绝不发送；身份存疑显示「身份存疑」，登录失效会标记账号。

每个账号使用独立的浏览器 Profile（`data/profiles/<accountId>/`），互不串号。

## 测试与构建

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest      # 后端测试（含假浏览器执行层测试）

cd ../frontend
npm run typecheck                          # TypeScript 严格检查
npm run build                              # 生产构建
```

## 当前能力边界

- ✅ 账号/契约对象/消息模板元数据 CRUD 骨架；
- ✅ 仪表盘聚合、账号启停、状态枚举与受控模板变量；
- ✅ Alembic 迁移 + 每日幂等唯一约束；
- ✅ 扫码登录流程（独立 Profile + 状态轮询）与 Dry Run（严格定位、绝不发送）；
- ✅ 凭据字段在任何 API 中一律拒绝（不接收 Cookie/密码/Token）；
- ⏳ 调度器、Worker、真实发送与发送终态确认（M3/M4）。

## 安全约定

- 只在本机运行，默认监听 `127.0.0.1`；
- 数据库与日志不保存任何抖音登录凭据；
- 浏览器不做指纹伪装，不自动处理验证码与风控；
- 验证码与安全验证只允许本人处理；
- 未来执行器默认开启防重复，发送终态未知时不自动重发。

## 文档

- [PRD](docs/PRD.md)
- [技术设计](docs/TECHNICAL_DESIGN.md)
- [API 文档](docs/API.md)
- [数据库 Schema](docs/SCHEMA.md)
- [调研报告](research-report.md)
- [项目状态](PROJECT_STATUS.md)
