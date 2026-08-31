# Task Plan: Flame Contractor — M6 可靠性闭环 → M7 前端重设计 → M8 体验功能

## Goal
M5 已全部完成并完成换机迁移（2026-08-29）。下一步按三期推进：**M6 补齐无人值守可靠性闭环**（诊断/重试/熔断/自启），**M7 前端重设计**（信息架构+视觉，使用 emilkowalski 技能与本地设计技能），**M8 体验功能**（文案池/统计/备份/截图扫码，长在新设计上）。

## Next Step
无强制待办（2026-08-31：全局调度暂停状态持久化已落地并验证，项目可继续按可选项迭代；交接提示词见 `docs/HANDOFF_PROMPT.md`）。可选后续：风控页检测（等真实样本）、多账号实测、统计图表可视化。

## Current Phase
M6/M7/M8 全部完成 + CRUD 补齐（好友启停/模板编辑删除/账号删除改名/unknown 人工闭环/扫描会话导入+抖音官方火花识别）。前端采用 D1「驾驶舱」方向（用户拍板）；全局调度暂停状态持久化；全量 131 passed + build 通过，关键功能真机实测

## 里程碑总览
- [x] **M1 骨架**（`9d535c4`）：FastAPI + SQLModel + SQLite + Alembic 控制平面，核心表与幂等约束，CRUD API，Vercel 风格控制台骨架。
- [x] **M2 登录与定位**（`4081936` + 定位修复 `2635044`→`b1a8712`）：每账号独立 Profile、扫码登录、严格定位（搜索+滚动+懒加载适配）、可见会话诊断。
- [x] **M3 真实发送**（`6acd24f` + `6544e04`）：预览/终态确认/好友删除/任务落库；实机联调修复三连坑。
- [x] **M4 调度器**（HEAD `3684462`）：自动每日发送。方案 A——账号级窗口随机取时。
- [x] **M5 通知（Bark）**（HEAD `226ee6e`）：异常推送接入；`trust_env=False` 修复 SOCKS 代理。
- [x] **M5 图片发送 + 火花校准**（HEAD `18d9115`）：图片模板/发送链路/确认 + 火花天数手动校准。
- [x] **M5 selector bundle 热更新**（HEAD `d687f88`）：选择器外置 JSON bundle + mtime 惰性重载 + `GET/POST /api/selectors`，改版不重启进程。
- [x] **M5 原生表情发送**（HEAD `5ff9e77` + `0249a38`）：send_sticker 参考 unmev，真机核对选择器 + 修复待渲染/确认 Bug，E2E 通过。「比心」真实发到 oinzc 小号。

## M6 可靠性闭环（后端为主，前端不动）
- [x] **M6-1 失败截图 + 工件增强**：发送失败/unknown 时自动截图存 `artifacts/<账号>/`；工件 JSON 增加 page_url、selector 探测结果、bundle/执行器版本；artifacts 保留期清理（`FLAME_ARTIFACT_RETENTION_DAYS`，默认 30）。
- [x] **M6-2 网络预检 + 有界重试 + 发送冷却 + 窗口外补发策略**：run_send 前置连通性预检（离线不消耗本轮、留待下个 tick）；failed_retryable 在窗口内有限次退避重试（默认 2 次、间隔 10min）；好友间发送冷却（默认 3s）；窗口已结束的到期计划转 `skipped` 而非深夜补发（`FLAME_WINDOW_EXPIRY_POLICY`）。
- [x] **M6-3 连续失败熔断 + 成功汇总推送**：`Account.failure_streak` 列（迁移）；连续 N 天失败（默认 3）转 `cooldown` + Bark 汇总通知，成功清零；run_send 完成后可选成功推送（`FLAME_NOTIFY_ON_SUCCESS`，默认开）。
- [x] **M6-4 开机自启**：`scripts/install_autostart.bat`（schtasks 登录时拉起前后端）+ README 说明。
- [x] **M6-5 全局调度暂停状态持久化**：新增 `AppSetting`/Alembic 迁移，暂停/恢复写入数据库，后端启动恢复，避免重启后意外恢复自动发送。

## M7 前端重设计
- [x] **M7-1 设计方向提案【用户拍板】**：加载 emil-design-eng / frontend-design / animation 系技能，产出 2–3 个完整方向（含预览页）供选择。
- [x] **M7-2 实施重设计**：信息架构重排 + 视觉升级 + 任务详情（含截图查看）；pytest/build 通过后提交。

## M8 体验功能（长在新设计上）
- [x] **M8-1 文案池 / 按星期文案**：`random_pool` 模板类型（多条候选轮换/随机）；模板页编辑。
- [x] **M8-2 统计 + CSV 导出**：成功率历史、连续天数、按日聚合；CSV 导出。
- [x] **M8-3 导入/导出备份**：契约+模板+运行记录 JSON 导出/导入。
- [x] **M8-4 截图扫码登录**：headless 渲染登录页 + 实时二维码网页（2026-08-29 实测路径），账号卡片按钮。
- [x] **M8-5 全量回归 + 文档**：pytest/build/启动验证；PROJECT_STATUS/README 更新。

## 不做清单（含原因，防止反复纠结）
| 项 | 结论 |
|---|---|
| Docker 部署 | 单机自用，M6-4 开机自启已覆盖；转远程时再议 |
| Playwright Trace | 截图覆盖八成诊断价值；不够再议 |
| 多 worker 进程隔离 | 当前 1 账号，进程内串行够用；账号多了再做 |
| platform_user_id 稳定匹配 | 触发条件再做：好友重名/频繁改名时 |
| selector bundle 版本管理 | JSON 在 git 内，回滚=改回去 |
| Profile 加密 | 与 PRD 边界一致（本机单用户），文档化风险即可 |
| 磁盘治理 | 已并入 M6-1 保留期清理 |
| 验证码/风控页检测 | 无真实样本无法写 selector；等 M6-1 截图留证后再做（联动） |
| 通知渠道扩展（钉钉/邮件） | 2026-08-28 已评估：仅 Bark |


## M4 已完成（验收要点）
- 新增 `SendSchedule`（账号级每日调度，`(account, business_date)` 唯一）+ Alembic 迁移 `15a5feca9ee1`。
- `app/scheduler.py`：懒生成当日计划（账号窗口内随机取时）、到期调度、账号级租约互斥、崩溃恢复（中间态转 unknown）、失败分类、可控暂停。
- `app/main.py` lifespan 挂载 `AsyncIOScheduler`，执行器复用 `browser_manager.run_send`；`requirements.txt` 加 `apscheduler>=3.10,<4`。
- `app/api/scheduler.py`：`GET /api/scheduler`、`POST /api/scheduler/{pause,resume}`。
- 前端 DashboardPage「自动调度」卡片：启停开关 + 今日各账号计划时刻与状态。
- **验证**：后端 pytest 131 passed；前端 build 通过；实际启动 uvicorn 后 `/health=ready`、`/api/scheduler` 返回真实账号今日计划（business_date 正确、scheduled_at 落在窗口内）。
- **完整真机 E2E**：到期计划 → `Scheduler.tick()` 派发 1 个账号 → `BrowserManager.run_send` 真实发送与终态确认 → `SendSchedule.succeeded` + `SendJob.succeeded`；二次 tick `dispatched=0` 且不调用发送执行器。

## M5 子阶段
### Phase 1: 通知适配器
- [x] Bark 通知接入（实现 + `trust_env=False` 修复 SOCKS 代理）；异常推送已验证。
- [x] 通知渠道：经评估决定不扩钉钉/邮件，保持仅 Bark（用户 2026-08-28 决定）。
- [x] 通知配置安全存储：Bark 密钥只存本机 `.env`（gitignore），不入库、不上传。
### Phase 2: 发送内容增强
- [x] **图片发送**：`send_image`（点附件图标 → filechooser → 确认弹窗「发送」）+ `image_bubble_confirmed` 数量锚定确认；实机验证真实发出。
- [x] **原生表情**（抖音原生 sticker，非图片）。
### Phase 3: 可靠性与诊断
- [x] selector bundle 热更新（页面改版快速适配）。
- [x] **火花天数手动校准**（`PATCH /api/targets/{id}` + 好友页「校准」按钮；不 DOM 读取）。
### Phase 4: 验收与交接
- [x] 后端测试 131 passed；前端 build；图片/原生表情 E2E 实机验证通过；调度暂停状态持久化回归通过。
- [x] 完整自动调度真机 E2E：到期计划 → `tick` 派发 1 个账号 → 真实发送与终态确认 → `SendSchedule.succeeded` + `SendJob.succeeded`；二次 `tick` `dispatched=0` 且不调用发送执行器。
- [x] 更新 PROJECT_STATUS / task_plan / docs。

## M4 关键技术决策
- **调度粒度 = 账号级随机（方案 A）**，对齐 `docs/TECHNICAL_DESIGN`（每账号一个 worker + 同账号并发=1）与调研（账号窗口随机）。每账号在窗口内取一个时刻，到点批量发该账号所有启用好友；不做好友级各自随机（需多次开浏览器会话，违背既定设计且实现更重）。
- 调度器只决定「账号今天何时发」，好友级发送/幂等完全复用 `run_send`（`(account, target, business_date, message_slot_id)` 唯一约束）。
- SQLite 读出的 datetime 为 naive：调度器内统一 `_utc_naive` 归一化再比较。
- 崩溃恢复优先「宁可 unknown 不误发」，部分结果转 `unknown_requires_review` 绝不自动重发。
- 调度器为进程内内存循环（APScheduler），持久调度语义由 `SendSchedule` 表承载。

## 复用 Success Criteria 参考（M3 保持）
- 后端 pytest **131 passed**；前端 build 通过；真实发送「测试111」送达小号；重复发送返回 duplicate_succeeded；完整自动调度真机 E2E 的计划与任务均为 succeeded。

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| 真实 Playwright 无 type_text/press_key → 发送 AttributeError | 1 | keyboard.insert_text + hasattr 分支（含 RealApiPage 回归测试） |
| 新版编辑器按 Enter 不发送 → 消息困输入框、终态超时 | 1 | 优先点发布按钮 messageMsgInputpublishBtn，Enter 兜底 |
| 输入框发送后残留零宽字符 \u200b，strip() 判不清空 | 1 | 空白判定纳入 \u200b\u200c\u200d\ufeff + 气泡内容确认兜底 |
| 预热滚动传选择器字符串而非元素句柄 → 静默空转 | 1 | query_selector 取元素再 evaluate |
| SQLite datetime naive vs aware 比较报错（调度器） | 1 | 统一 `_utc_naive` 归一化 |
| lifespan 闭包内 rebind 参数引发 UnboundLocalError | 1 | 用局部变量 `sched` 承接，不 rebind 函数参数 |
| 图片确认把头像当图片气泡 → 未发出图误判 succeeded | 2 | 用 `image_bubble_confirmed`：排除头像 + 发送前后数量锚定 |
| 图片直接注入 input[type=file] 发不出（确认弹窗） | 2 | 改「点附件图标 → filechooser → 确认弹窗「发送」」 |
| 图片气泡出现慢 → 已发出记 unknown | 1 | `_confirm_send` 图片分支轮询 `image_bubble_confirmed` |

## 注意事项
- 调度器与手动发送共用幂等与互斥，确保「自动续火」不重复打扰好友。
- DOM 选择器依赖抖音页面结构（selectors.py），页面改版需适配；无把握时宁可记 unknown 不误发。
- 本机服务仅监听 127.0.0.1，勿暴露公网；转远程需先加鉴权与加密。
