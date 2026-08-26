# 焰之契约者 · Flame Contractor

> 你与它立下契约，每日以一言续之，焰不可熄。

本机优先的多账号抖音续火控制台。当前为 **0.1.0 骨架版本**：控制平面、SQLite 数据模型与 React 控制台已可运行，抖音执行器与调度器尚未接入。

## 目录结构

```text
backend/    FastAPI + SQLModel + SQLite 控制平面
frontend/   React + TypeScript + Vite 本地控制台
docs/       PRD、技术设计、API 文档、数据库 Schema
```

## 启动

### 1. 后端

```powershell
cd backend
python -m venv .venv            # 首次
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # 首次
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动自动创建 `data/flame-contractor.db`（SQLite WAL，父目录自动建立）。

- 健康检查：<http://127.0.0.1:8000/health>
- OpenAPI 文档：<http://127.0.0.1:8000/docs>

### 2. 前端

```powershell
cd frontend
npm install          # 首次
npm run dev
```

打开 <http://localhost:5173>（开发服务器已代理 `/api` 与 `/health` 到后端）。

## 测试与构建

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest      # 后端测试

cd ../frontend
npm run typecheck                          # TypeScript 严格检查
npm run build                              # 生产构建
```

## 当前能力边界

- ✅ 账号/契约对象/消息模板元数据 CRUD 骨架；
- ✅ 仪表盘聚合、账号启停、状态枚举与受控模板变量；
- ✅ 数据库唯一约束承载未来“每日防重复”设计；
- ✅ 凭据字段在任何 API 中一律拒绝（不接收 Cookie/密码/Token）；
- ⏳ 扫码登录、独立浏览器 Profile、调度器、Worker 与真实发送（下一阶段）。

## 安全约定

- 只在本机运行，默认监听 `127.0.0.1`；
- 数据库与日志不保存任何抖音登录凭据；
- 验证码与安全验证只允许本人处理，不自动绕过；
- 未来执行器默认开启防重复，发送终态未知时不自动重发。

## 文档

- [PRD](docs/PRD.md)
- [技术设计](docs/TECHNICAL_DESIGN.md)
- [API 文档](docs/API.md)
- [数据库 Schema](docs/SCHEMA.md)
- [调研报告](research-report.md)
- [项目状态](PROJECT_STATUS.md)
