# Task Plan: Flame Contractor — M3 完成，启动 M4 调度器

## Goal
M3（真实发送 + 终态确认）已完成并实机验证。当前目标：进入 M4——让系统按账号发送时段自动向启用好友发送，实现「自动续火」。

## Next Step
实现 Scheduler：按 account.timezone + send_window 生成每日任务，窗口内随机取时，数据库租约 + heartbeat + 账号互斥；后端挂定时调度（本地单进程，APScheduler 或自建 asyncio 循环）。

## Current Phase
Phase 1（M4）

## 里程碑总览
- [x] **M1 骨架**（`9d535c4`）：FastAPI + SQLModel + SQLite + Alembic 控制平面，核心表与幂等约束，CRUD API，Vercel 风格控制台骨架。
- [x] **M2 登录与定位**（`4081936` + 定位修复 `2635044`→`b1a8712`）：每账号独立 Profile、扫码登录、严格定位（搜索+滚动+懒加载适配）、可见会话诊断。
- [x] **M3 真实发送**（`6acd24f` + `6544e04`）：预览/终态确认/好友删除/任务落库；实机联调修复三连坑（协议泄漏、Enter 不发送、零宽字符），真实发送验证通过。
- [ ] **M4 调度器**（当前）：自动每日发送。
- [ ] **M5 增强**：原生表情/图片、通知、selector 热更、火花天数读取校准。

## M4 子阶段
### Phase 1: 调度与任务生成（in_progress）
- [ ] 设计「每日任务生成」：遍历启用账号，按 timezone 计算当日 business_date，为每个启用契约对象生成 SendJob（复用已有幂等唯一约束）。
- [ ] Scheduler 进程：本地 asyncio 循环或 APScheduler；每账号独立发送时段窗口；窗口内随机取执行时间。
- [ ] 数据库租约：scheduled→leased（lease_owner/lease_expires_at）+ heartbeat，防重复触发与 worker 崩溃恢复。
- [ ] 账号互斥：同账号同一时间只跑一个发送流程。
- **Status:** pending

### Phase 2: 安全与恢复
- [ ] 崩溃恢复：租约过期且已进入 sending/confirming 的 → 转 unknown_requires_review，不自动重发；仅 scheduled/未开始的可重排。
- [ ] 失败重试：failed_retryable 在当天窗口内有限次退避重试；unknown 一律人工核销（用 `finalize_unknown_jobs.py`）。
- **Status:** pending

### Phase 3: 前端联动
- [ ] 总览「今日进度」改为读取真实 SendJob 状态；任务记录页完整展示。
- [ ] 手动「立即发送」与自动任务共用同一套幂等约束（不重复）。
- **Status:** pending

### Phase 4: 验收与交接
- [ ] 后端测试全绿；真实账号跨天场景验证（次日自动任务生成）。
- [ ] 更新 PROJECT_STATUS / docs / README；说明可暂停全部自动任务。
- **Status:** pending

## M3 已完成状态（复用 Success Criteria 参考）
- 后端 pytest **39 passed**；前端 build 通过；真实发送「测试111」送达小号；重复发送返回 duplicate_succeeded。
- 已在真实账号完成验收（五页视觉质检无缺陷 + 数据落库正确）。

## 关键技术决策
- 登录态/会话存于持久浏览器 Profile；身份确认靠右侧标题严格等值，存疑即 ambiguous 不发。
- 发送终态采用「发布按钮触发 + 编辑框清空（零宽感知）+ 气泡内容确认」三重兜底；无可靠信号记 unknown，绝不误判成功。
- `unknown_requires_review` 当日禁止自动重发；人工核销走 `scripts/finalize_unknown_jobs.py`。
- 调度用本地单进程（不引 Celery/Redis/SaaS 编排）；同账号互斥。
- 不加载任何反检测脚本；验证码与风控仅本人处理。

## Errors Encountered（M3 关键，防重踩）
| Error | Attempt | Resolution |
|-------|---------|------------|
| 真实 Playwright 无 type_text/press_key → 发送 AttributeError | 1 | keyboard.insert_text + hasattr 分支（并加 RealApiPage 回归测试防协议泄漏） |
| 新版编辑器按 Enter 不发送 → 消息困输入框、终态超时 | 1 | 优先点击发布按钮 messageMsgInputpublishBtn，Enter 兜底 |
| 输入框发送后残留零宽字符 \u200b，strip() 判不清空 | 1 | 空白判定纳入 \u200b\u200c\u200d\ufeff + 气泡内容确认兜底 |
| 预热滚动传选择器字符串而非元素句柄 → 静默空转 | 1 | query_selector 取元素再 evaluate |
| 脚本 import run_migrations 从 app.core.database（错 3 次） | 3 | 正确路径是 app.core.migrations |

## 注意事项
- 调度器与手动发送共用幂等与互斥，确保「自动续火」不重复打扰好友。
- DOM 选择器依赖抖音页面结构（selectors.py），页面改版需适配；无把握时宁可记 unknown 不误发。
- 本机服务仅监听 127.0.0.1，勿暴露公网；转远程需先加鉴权与加密。
