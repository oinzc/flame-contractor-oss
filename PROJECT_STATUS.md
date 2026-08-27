# PROJECT_STATUS — Flame Contractor（焰之契约者）

> 最近更新：2026-08-27 | 由 DeepSeek Harness Agent 更新 | HEAD = `226ee6e`

## 当前状态
**M4 已完成并通过后端测试 + 端到端验证**：自动每日调度发送接入——每账号窗口内随机取时、账号级租约互斥、崩溃恢复（中间态转 unknown 不自动重发）、可控暂停，复用 M3 已验证的 `run_send` 发送链路。**异常推送（Bark）也已接入**。

**下一目标（M5）**：原生表情/图片、selector 热更新、火花天数手动校准（见下方决策说明）。

**火花天数说明（已实机核实）**：`streak_days` 为本地累计值（发送成功 +1），**非抖音实时读取**。参考 ScriptCat-Douyin-Fire-Helper 同样采用"本地记录 + 每日 +1 + 手动调整"，抖音网页版不提供稳定、对所有好友都存在的火花天数 DOM（oinzc 会话实测无火花提示，且火花文案格式多变、类名被多会话共用，DOM 读取会串行误读，违背"宁可不发不错发"边界）。

## 已完成内容（按里程碑）

### M1 骨架（`9d535c4`）
- FastAPI + SQLModel + SQLite WAL 控制平面；核心表 Account/ContractTarget/MessageTemplate/SendJob/SendAttempt。
- Alembic 迁移（基线 `722cd9dc7d0b`），启动自动建表；每日幂等唯一约束 `(account, target, business_date, message_slot_id)`。
- API：health、dashboard 聚合、accounts、targets、templates；凭据字段一律 422；模板仅 4 个受控变量。
- React 19 + Vite 8 控制台。

### M2 登录与定位（M2 提交：`4081936`，后经多轮定位修复）
- 每账号独立持久浏览器 Profile（`launch_persistent_context`），登录态零粘贴 Cookie。
- 扫码登录流程：headed 弹窗、180s 超时、状态轮询/取消；登录成功标记 healthy，失效标记 auth_expired。
- Dry Run / 定位校验：严格匹配 + 右侧标题二次核对，记 found/ambiguous/not_found；绝不发送。
- 定位链路实机修复（6 个提交）：无历史会话联系人须搜索+点「发消息」；会话列表/搜索索引分页懒加载须先滚动预热；预热须传元素句柄；搜索结果须轮询等待；输出「本次可见会话」诊断。

### M3 真实发送（`6acd24f` + `6544e04`）
- 模板渲染（4 受控变量）；发送流程：严格定位 → 输入 → 触发发送 → 终态确认。
- 新增：发送预览接口/弹窗、好友删除接口、任务落库与总览联动、SendJob/SendAttempt 状态机。
- 实机联调修复三连坑：真实 Playwright 无 `type_text/press_key` → `keyboard.insert_text` + hasattr 分支；新版编辑器按 Enter 不发送 → 优先点「发布按钮」；输入框残留零宽字符 → 空白判定纳入 `\u200b\u200c\u200d\ufeff`。
- 权威成功信号：渲染文本进入消息列表气泡即确认；unknown+气泡证据升级为成功（防重复发送）。

### M4 自动调度（本轮新增，HEAD `3684462`）
- 新增 `SendSchedule` 表（账号级每日调度）+ Alembic 迁移 `15a5feca9ee1`。
- `app/scheduler.py`：懒生成当日计划（窗口内随机取时）、到期调度、账号级租约互斥、崩溃恢复、失败分类、可控暂停。
- `app/main.py` lifespan 挂载 `AsyncIOScheduler`（进程内，注入 `browser_manager.run_send` 作为执行器）。
- 部署：`requirements.txt` 追加 `apscheduler>=3.10,<4`。
- 新增 `app/api/scheduler.py`：`GET /api/scheduler`（状态+今日计划）、`POST /api/scheduler/{pause,resume}`。
- 前端 DashboardPage「自动调度」卡片：启停开关 + 今日各账号计划时刻与状态。

## 关键技术决策
- 登录态存于持久 Profile；登录检测=会话列表出现；身份确认=右侧标题严格等值，存疑即 ambiguous 不发。
- 同账号浏览器流程互斥（登录/Dry Run/发送）；`unknown_requires_review` 禁止当日自动重发。
- 发送确认不依赖「气泡出现=成功」，采用「发布按钮触发 + 编辑框清空（零宽感知）+ 气泡内容确认」三重兜底。
- **M4 调度粒度 = 账号级随机**（对齐 `docs/TECHNICAL_DESIGN` 的「每账号一个 worker」+「同账号并发=1」+ 调研的「账号窗口随机」）：每账号在窗口内取一个时刻，到点由同一浏览器会话批量发送该账号所有启用好友，而非每好友各自随机时刻。理由：复用已验证链路、改动最小、零迁移风险；好友级随机需多次开浏览器会话，违背项目既定设计且实现更重。
- 调度器只决定「账号今天何时发」，好友级发送与幂等（`run_send` 的 `(account, target, business_date, message_slot_id)`）完全复用。
- SQLite 读出的 datetime 为 naive：调度器内统一用 `_utc_naive` 归一化后再比较，避免 aware/naive 比较错误。
- 迁移替换 create_all；真实运行与测试均走 `alembic upgrade head`。
- **火花天数 = 本地累计（`streak_days` 发送成功 +1），非抖音实时读取**：抖音网页版不提供稳定、对所有好友存在的火花天数 DOM；参考 ScriptCat-Douyin-Fire-Helper（本地记录+每日+1+手动调整）。DOM 读取已实机验证不可靠（误读风险 > 收益），放弃。
- 不加载任何反检测脚本；验证码与风控仅本人处理。

## 当前核心代码结构
```
backend/app/
  core/        config(database_url/profiles_dir/artifacts_dir)、database、migrations
  models.py    Account/ContractTarget/MessageTemplate/SendJob/SendAttempt/SendSchedule + 枚举
  schemas.py   API 输入/输出模型（camelCase 别名字段）+ SendScheduleRead/SchedulerStatusRead
  services.py  账号/模板/好友 CRUD、发送预览、仪表盘聚合
  scheduler.py M4 调度器：懒生成/窗口随机/租约互斥/崩溃恢复/暂停
  execution/   profile_store、selectors、douyin_chat、send_confirmer、message_renderer、browser_manager
  api/         accounts、targets、templates、dashboard、execution、scheduler、health、router
backend/alembic/versions/  baseline(722cd9dc7d0b)、add_send_schedule(15a5feca9ee1)
backend/scripts/  search_debug / check_state / verify_sent / probe_dry_run / probe_send /
                   reset_unknown_jobs / finalize_unknown_jobs（实机排查/后台账工具）
frontend/src/
  App.tsx(顶栏导航+页面切换+全局刷新+scheduler 状态)、components/DashboardPage/AccountsPage/
  TargetsPage/TemplatesPage/JobsPage/CreateAccountModal/SendModal/StatusBadge
  lib/format.ts(状态文案映射+调度映射)、api.ts、types.ts、theme.css(Vercel 浅色)
```

## 测试与验证结果
| 项目 | 结果 |
|---|---|
| 后端 pytest | **49 passed**（39=CRUD/迁移/登录/定位/Dry Run/发送终态/幂等/RealApiPage 回归/零宽清空/气泡升级；10=调度：生成/窗口随机/到期触发/账号失效不派发/partial→unknown/租约过期恢复/中间态 job 恢复/暂停/端到端经 run_send 真实发送） |
| 前端 npm run build | tsc --noEmit + vite build 通过 |
| 端到端启动 | uvicorn 启动后 `/health=database:ready`；`/api/scheduler` 返回 enabled=true、真实账号今日计划（business_date=2026-08-27、scheduled_at 落在账号窗口内、status=pending） |
| M3 真实发送 E2E | 测试111 送达小号；重复发送正确跳过（M3 保留，未回归） |

## 已知问题 / 边界
- 发送/定位的 DOM 选择器依赖抖音当前页面结构（`selectors.py`），页面改版可能失效；`SendConfirmer` 在无可靠信号时记 unknown 而非误判成功，是安全设计。
- 真实发送的「终态确认」以气泡内容 + 发布按钮为主，`SendStatusretry` 等失败标记选择器未经验证，失败标记可能检测不到（会退化为 unknown 而非误判成功）。
- Profile 数据高敏感，仅依赖系统用户权限，未做额外加密。
- Starlette httpx→httpx2 弃用警告存在，功能无影响。
- **调度器为进程内内存循环（APScheduler），未用持久化 jobstore**：进程重启后后台循环自动重跑 `tick`，会重新生成当日/补跑当日计划（幂等键兜底不会重复发送）。真正需要的持久调度语义由 `SendSchedule` 表承载（唯一约束 + 租约字段），重启安全。
- 调度默认每 60s 轮询一次，到点判定依赖 `scheduled_at`；若进程在窗口内长时间挂起，重启后可能补发当日计划（幂等兜底）。

## 尝试过但失败的方案
- **火花天数 DOM 读取（已放弃）**：尝试从抖音会话页读取真实火花天数，结论是不可靠——火花提示只在会话列表项的特定标签里出现、文案随状态变化（为你恢复 N 天火花 / 重燃中 N/M / 已连续 N 天）、不是每个好友都有（oinzc 实测无提示），且类名被多会话共用、抓整页会串行误读。读错天数发给好友比不读更糟，违背"宁可不发不错发"。采纳参考项目 ScriptCat 的"本地累计 + 手动调整"做法。
- M3 期间的实测坑（协议泄漏/Enter 不发送/零宽字符/预热传选择器/迁移 import 路径）见 `task_plan.md`「Errors Encountered」。

## 下一步开发顺序
1. **M5 增强**：原生表情/图片发送、selector 热更新、通知补充渠道（钉钉/邮件）、火花天数手动校准（参考 ScriptCat，提供手动校正入口）。
2. 若转多用户/远程：加鉴权与加密（当前仅监听 127.0.0.1，勿暴露公网）。

## 应急运维工具（backend/scripts，实机排查用）
- `probe_dry_run.py <account_id>`：走产品代码路径跑一次定位校验（只读不发）。
- `probe_send.py <account_id>`：走产品代码路径实际发送一次。
- `check_state.py <account_id> <text>`：区分消息困在编辑器 vs 已进气泡；核对发送按钮选择器。
- `verify_sent.py <account_id> <text>`：全文检索文案是否已出现在会话里。
- `finalize_unknown_jobs.py` / `reset_unknown_jobs.py`：人工核对后核销/重置当日任务状态。
