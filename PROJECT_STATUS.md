# PROJECT_STATUS — Flame Contractor（焰之契约者）

> 最近更新：2026-08-26 | 由 DeepSeek Harness Agent 更新

## 当前目标
本机自用版「PRD + 技术设计 + 可运行骨架」已交付；下一里程碑为 M2（单账号扫码登录 + 独立 Profile + Dry Run）。

## 已完成内容
- 调研：核验 9 个候选项目，确定 `unmev/douyin-auto-fire` 为首要代码/测试参考，详见 `research-report.md`。
- 文档：`docs/PRD.md`、`docs/TECHNICAL_DESIGN.md`、`docs/API.md`、`docs/SCHEMA.md`。
- 后端：FastAPI + SQLModel + SQLite，核心表 Account/ContractTarget/MessageTemplate/SendJob/SendAttempt，含每日幂等唯一约束 `(account_id, target_id, business_date, message_slot_id)`。
- 后端 API：health、dashboard 聚合、accounts（创建/列出/PATCH，凭据字段一律 422）、targets、templates（受控变量白名单）。
- 前端：React 19 + TypeScript + Vite 8，契约炉心视觉（Coal/Iron/Parchment/Ember），签名元素「今日焰线」节点轨道；账号创建、暂停/恢复、空态与错误横幅。
- 验证：后端 10/10 测试通过；前端类型检查与生产构建通过；浏览器端到端流程与截图视觉质检通过。
- 演示数据已清空，交付时为全新数据库状态。

## 关键技术决策
- 本机自用版：FastAPI + SQLite(WAL) + React/Vite，单机优先，不引入 Redis/Celery。
- 凭据零落地：骨架阶段 Account 模型不含 Cookie/密码/Token 字段，API `extra="forbid"`。
- 幂等先于执行：即使 Scheduler 尚未接入，数据库唯一约束已就位。
- 状态机：账号 8 态、任务 10 态；`unknown_requires_review` 不可自动重发。
- 模板仅允许 `{{target_name}} {{streak_days}} {{weekday}} {{date}}` 四个受控变量。
- 日志/数据库字段 snake_case，API camelCase；时间戳 ISO 8601。
- UI 明确标注未实现能力（扫码登录、调度器、执行器均为“下一阶段”），不伪装可用。

## 修改过的核心文件
- `backend/app/`：main、models、schemas、services、core/config、core/database、api/{health,accounts,targets,templates,dashboard,router}
- `backend/tests/`：conftest、test_api
- `backend/requirements.txt`、`requirements-dev.txt`、`pyproject.toml`、`.env.example`
- `frontend/`：package.json、tsconfig、vite.config、index.html、src（App、theme.css、api、types、lib/format、6 个组件）
- `docs/`：PRD、TECHNICAL_DESIGN、API、SCHEMA
- `README.md`、`.gitignore`、`PROJECT_STATUS.md`、`task_plan.md`、`progress.md`

## 测试与验证结果
| 项目 | 结果 |
|---|---|
| 后端 pytest | 10 passed（health、账号 CRUD/409/422、模板变量、契约对象、仪表盘聚合、幂等唯一约束） |
| 前端 npm run build | tsc --noEmit + vite build 通过 |
| API 冒烟（真实 uvicorn） | health/accounts/templates/targets/dashboard 全部正确 |
| 浏览器端到端（Vite + pilot） | 新建账号、暂停/恢复、焰线节点、空态渲染均生效 |
| 视觉质检（截图 + modlens） | 布局无重叠、无文字截断、节点正常 |
| Git | 已 `git init`（main 分支），全部文件尚未提交 |

## 已知问题
- 骨架不含抖音执行能力：扫码登录、好友搜索、发送、终态确认均未实现（M2/M3 范围）。
- 无用户鉴权：当前仅监听 127.0.0.1，切勿暴露公网；桌面封装前不可远程使用。
- SQLite 用 `create_all()` 初始化；进入 M2 前应引入 Alembic 基线迁移。
- SendJob/SendAttempt 表已建但暂无调度器写入路径。
- 删除/编辑契约对象与模板的 API 尚未实现（骨架期故意省略，避免误删恢复成本）。
- Starlette 提示 httpx→httpx2 迁移 deprecation warning，功能不受影响，可后续处理。
- QA 截图保存在 `artifacts/qa/`（gitignored），可自行删除。

## 尝试过但失败的方案
- 调研阶段两个后台 subagent 无结果即失败 → 改主会话直接核验，不再重复该委派。
- 直接读取抖音开放平台旧“发送私信”页面 → 404，无法据搜索摘要断言官方 API 可用于个人续火。

## 下一步开发顺序
1. 提交当前骨架为首个 commit（用户确认后）。
2. M2：引入 Alembic；每账号独立持久 Profile 目录与扫码登录流程；Dry Run。
3. M3：严格好友绑定（优先 platform_user_id）、纯文字发送、发送终态确认与失败 artifact。
4. M4：Scheduler（随机焰窗、租约、heartbeat）+ 每账号 Worker 进程隔离。
5. M5：原生表情/图片、通知、selector 热更与火花天数。
