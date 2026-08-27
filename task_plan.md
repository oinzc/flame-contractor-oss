# Task Plan: Flame Contractor — M4 完成，M5 通知已接入，继续 M5 增强

## Goal
M4（自动每日发送）+ Bark 异常通知已完成并验证。当前目标：继续 M5——原生表情/图片、selector 热更、火花天数手动校准、通知补充渠道。

## Next Step
实现 M5 增强：原生表情/图片发送、selector 热更新、火花天数手动校准（参考 ScriptCat）、通知补充渠道（钉钉/邮件）。

## Current Phase
Phase 1（M5）

## 里程碑总览
- [x] **M1 骨架**（`9d535c4`）：FastAPI + SQLModel + SQLite + Alembic 控制平面，核心表与幂等约束，CRUD API，Vercel 风格控制台骨架。
- [x] **M2 登录与定位**（`4081936` + 定位修复 `2635044`→`b1a8712`）：每账号独立 Profile、扫码登录、严格定位（搜索+滚动+懒加载适配）、可见会话诊断。
- [x] **M3 真实发送**（`6acd24f` + `6544e04`）：预览/终态确认/好友删除/任务落库；实机联调修复三连坑。
- [x] **M4 调度器**（本轮，HEAD `3684462`）：自动每日发送。方案 A——账号级窗口随机取时。
- [x] **M5 通知（Bark）**（HEAD `226ee6e`）：异常推送接入；`trust_env=False` 修复 SOCKS 代理。
- [ ] **M5 增强**：原生表情/图片、selector 热更、火花天数手动校准、通知补充渠道。

## M4 已完成（验收要点）
- 新增 `SendSchedule`（账号级每日调度，`(account, business_date)` 唯一）+ Alembic 迁移 `15a5feca9ee1`。
- `app/scheduler.py`：懒生成当日计划（账号窗口内随机取时）、到期调度、账号级租约互斥、崩溃恢复（中间态转 unknown）、失败分类、可控暂停。
- `app/main.py` lifespan 挂载 `AsyncIOScheduler`，执行器复用 `browser_manager.run_send`；`requirements.txt` 加 `apscheduler>=3.10,<4`。
- `app/api/scheduler.py`：`GET /api/scheduler`、`POST /api/scheduler/{pause,resume}`。
- 前端 DashboardPage「自动调度」卡片：启停开关 + 今日各账号计划时刻与状态。
- **验证**：后端 pytest 49 passed；前端 build 通过；实际启动 uvicorn 后 `/health=ready`、`/api/scheduler` 返回真实账号今日计划（business_date 正确、scheduled_at 落在窗口内）。

## M5 子阶段（草拟）
### Phase 1: 通知适配器
- [x] Bark 通知接入（实现 + `trust_env=False` 修复 SOCKS 代理）；异常推送已验证。
- [ ] 补充渠道：钉钉/邮件通知（可选）。
- [ ] 通知配置与其安全存储（令牌不入库、不上传）。
### Phase 2: 发送内容增强
- [ ] 原生表情、图片发送（复用统一终态确认接口，不各自 sleep）。
### Phase 3: 可靠性与诊断
- [ ] selector bundle 热更新（页面改版快速适配）。
- [ ] 火花天数**手动校准**（参考 ScriptCat：本地累计 + 提供手动调整入口；不 DOM 读取）。
### Phase 4: 验收与交接
- [ ] 后端测试全绿；前端 build；真实账号验证新增能力。
- [ ] 更新 PROJECT_STATUS / docs / README。

## M4 关键技术决策
- **调度粒度 = 账号级随机（方案 A）**，对齐 `docs/TECHNICAL_DESIGN`（每账号一个 worker + 同账号并发=1）与调研（账号窗口随机）。每账号在窗口内取一个时刻，到点批量发该账号所有启用好友；不做好友级各自随机（需多次开浏览器会话，违背既定设计且实现更重）。
- 调度器只决定「账号今天何时发」，好友级发送/幂等完全复用 `run_send`（`(account, target, business_date, message_slot_id)` 唯一约束）。
- SQLite 读出的 datetime 为 naive：调度器内统一 `_utc_naive` 归一化再比较。
- 崩溃恢复优先「宁可 unknown 不误发」，部分结果转 `unknown_requires_review` 绝不自动重发。
- 调度器为进程内内存循环（APScheduler），持久调度语义由 `SendSchedule` 表承载。

## 复用 Success Criteria 参考（M3 保持）
- 后端 pytest **49 passed**；前端 build 通过；真实发送「测试111」送达小号；重复发送返回 duplicate_succeeded。

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| 真实 Playwright 无 type_text/press_key → 发送 AttributeError | 1 | keyboard.insert_text + hasattr 分支（含 RealApiPage 回归测试） |
| 新版编辑器按 Enter 不发送 → 消息困输入框、终态超时 | 1 | 优先点发布按钮 messageMsgInputpublishBtn，Enter 兜底 |
| 输入框发送后残留零宽字符 \u200b，strip() 判不清空 | 1 | 空白判定纳入 \u200b\u200c\u200d\ufeff + 气泡内容确认兜底 |
| 预热滚动传选择器字符串而非元素句柄 → 静默空转 | 1 | query_selector 取元素再 evaluate |
| SQLite datetime naive vs aware 比较报错（调度器） | 1 | 统一 `_utc_naive` 归一化 |
| lifespan 闭包内 rebind 参数引发 UnboundLocalError | 1 | 用局部变量 `sched` 承接，不 rebind 函数参数 |

## 注意事项
- 调度器与手动发送共用幂等与互斥，确保「自动续火」不重复打扰好友。
- DOM 选择器依赖抖音页面结构（selectors.py），页面改版需适配；无把握时宁可记 unknown 不误发。
- 本机服务仅监听 127.0.0.1，勿暴露公网；转远程需先加鉴权与加密。
