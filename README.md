# 焰之契约者 · Flame Contractor

> 你与它立下契约，每日以一言续之，焰不可熄。

本机优先的多账号抖音续火控制台。当前进度：**M4 已完成** —— 控制平面、扫码登录、严格好友定位、真实发送 + 发送终态确认、每日防重复，以及**自动每日调度发送**全部就绪，并在真实账号上验证通过。

## 目录结构

```text
backend/    FastAPI + SQLModel + SQLite + Playwright + Alembic + APScheduler
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

## 扫码登录与定位校验（M2）

1. 在控制台新建账号并添加好友；
2. 点击账号卡片上的「扫码登录」：本机弹出该账号专属浏览器窗口，用抖音 App 扫码；
3. 扫码成功后账号变为「正常」，再点「定位校验」验证每个好友能否被严格定位；
4. 定位校验只定位、绝不发送；身份存疑显示「身份存疑」，登录失效会标记账号。

每个账号使用独立的浏览器 Profile（`data/profiles/<accountId>/`），互不串号。

## 真实发送（M3）

1. 账号「正常」后点「立即发送」→ 弹出发送确认，逐好友展示要发送的文案；
2. 确认后发送：搜索/滚动定位好友 → 二次核对身份 → 输入文案 → 点发布按钮（新版编辑器按 Enter 不发送）；
3. 发送终态确认：编辑框清空 + 气泡内容出现才算成功；无可靠信号记「结果未确认」，当日禁止自动重发；
4. 每天每个好友只发一次（数据库幂等约束兜底）；成功后火花天数 +1。

## 自动每日发送（M4）

后端启动时自动挂载**进程内调度器**（APScheduler，`app/state/scheduler`）。它只负责「哪个账号今天何时发——账号级随机取时 + 数据库租约 + 同账号互斥 + 崩溃恢复」，实际每个好友的发送仍复用上面已验证的 `run_send` 链路。

- 每个启用账号在它自己的 `send_window`（默认 `20:00-22:00`，按账号 `timezone` 换算）内随机取**一个**发送时刻；
- 到点后由同一个浏览器会话批量发送该账号所有启用好友（同账号并发 = 1，绝不同时开两个会话）；
- 后台每 60 秒轮询一次：为每个启用账号懒生成当日计划（幂等，`(account, business_date)` 唯一），到点抢租约并触发发送；
- 结果确认走既有终态确认；`partial`/无法确认一律记 `unknown_requires_review`，**绝不自动重发**；
- **崩溃恢复**：租约过期且计划/好友任务仍停在中间态 → 转「待人工复核」；仅未开始的可重排；
- **暂停**：控制台「自动调度」卡片可一键暂停/恢复全部自动任务（暂停时手动发送仍可用，不影响）。

## 异常通知（M5）

自动调度在无人值守时，若遇到必须人工接管的信号，会推送到你的手机（当前支持 **Bark**）：

- 账号登录失效（`auth_expired`）；
- 调度发送中断 / 结果未确认（`unknown_requires_review`）；
- 部分好友结果未确认、发送异常。

配置：在 `backend/.env` 填入你的 Bark 设备地址（只存本机，不入库、不上传）：

```powershell
# backend/.env
FLAME_BARK_URL=https://api.day.app/<你的device_key>
```

未配置时通知自动跳过，调度照常运行。

## Selector 热更新（M5）

抖音页面随时可能改版，选择器（`app/execution/selectors_bundle.json`）已外置为 **JSON bundle**，运行时惰性检测文件 mtime——页面改版时只需编辑该 JSON（或调接口重载），**无需重启后端进程**，即将到达的登录/定位/发送会自动用新选择器。

```powershell
# 查看当前 bundle / 强制重载（编辑 selectors_bundle.json 后调用）
curl http://127.0.0.1:8000/api/selectors
curl -X POST http://127.0.0.1:8000/api/selectors/reload
```

安全约定不变：无把握的 selector 宁可记 unknown 不误发；改版后必须真实 E2E 复核。

## 测试与构建

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest      # 后端测试（含假浏览器执行层测试）

cd ../frontend
npm run typecheck                          # TypeScript 严格检查
npm run build                              # 生产构建
```

## 当前能力边界

- ✅ 账号/好友/消息模板管理；仪表盘聚合、账号启停、受控模板变量；
- ✅ Alembic 迁移 + 每日幂等唯一约束；
- ✅ 扫码登录（独立 Profile + 状态轮询）与定位校验（严格匹配、绝不发送）；
- ✅ 真实发送 + 发送终态确认 + 每日防重复；任务落库与总览联动；
- ✅ 自动每日调度发送（账号级窗口随机取时、租约互斥、崩溃恢复、可控暂停）；
- ✅ 异常通知（Bark：登录失效/发送中断/结果未确认主动推送）；
- ✅ 图片模板发送（点附件图标→选图→确认弹窗发送，实机验证）；
- ✅ 火花天数手动校准（本地累计 + 手动校正入口）；
- ✅ selector bundle 热更新（页面改版改 `selectors_bundle.json` 即热生效，无需重启进程）；
- ✅ 凭据字段在任何 API 中一律拒绝（不接收 Cookie/密码/Token）；
- ⏳ 原生表情、通知补渠道（钉钉/邮件）。

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
