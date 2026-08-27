# PROJECT_STATUS — Flame Contractor（焰之契约者）

> 最近更新：2026-08-27 | 由 DeepSeek Harness Agent 更新 | HEAD = `3684462`

## 当前状态
**M3 已完成并在真实账号上验证通过**：多账号扫码登录、独立 Profile、严格好友定位、真实发送 + 终态确认、每日防重复全部可用。

**下一目标（M4）**：Scheduler——每天在账号发送时段内随机取时，自动向启用好友发送，实现「自动续火」（最初的核心诉求）。

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
- **定位链路实机修复**（这是本轮最大工作量，6 个提交 `2635044`→`b1a8712`）：
  - 无历史会话联系人必须**搜索 + 点「发消息」按钮**（SearchPanelitemchat_btn）；
  - 抖音会话列表与搜索索引均为**分页懒加载**，必须先滚动预热再搜索；
  - 预热滚动必须传**元素句柄**（传选择器字符串会静默空转）；
  - 搜索结果可能在固定等待后才渲染，需**轮询等待**；
  - 输出「本次可见会话」诊断帮助用户核对昵称。

### M3 真实发送（`6acd24f` + `6544e04`）
- 模板渲染（4 受控变量）；发送流程：严格定位 → 输入 → 触发发送 → 终态确认。
- 新增：发送预览接口/弹窗、好友删除接口、任务落库与总览联动、SendJob/SendAttempt 状态机。
- **实机联调修复三连坑**（`6544e04`）：
  1. 假页面协议泄漏：`type_text/press_key` 真实 Playwright 无 → `AttributeError`；改 `keyboard.insert_text` + hasattr 分支。
  2. 新版编辑器按 Enter 不发送 → 消息困在输入框、终态超时；按 unmev 方案改为**优先点发布按钮** `messageMsgInputpublishBtn`，Enter 兜底。
  3. 输入框发送后残留**零宽字符** `\u200b`，`strip()` 判不清空 → 永不成功；空白判定纳入 `\u200b\u200c\u200d\ufeff`。
- 权威成功信号：渲染文本进入消息列表气泡即确认；unknown+气泡证据升级为成功（防重复发送）。

## 实机验证结果（老大「验收」通过）

用真实账号（`oinzc` 小号）：
- `测试111` 已送达小号会话（`check_state` 探针确认真实气泡）；
- 任务记录「已续焰」，火花天数=1，总览进度 100%；
- 重复发送返回 `duplicate_succeeded`（幂等防重闭环验证通过）；
- 五个页面（总览/账号/好友/模板/任务记录）逐页截图 + modlens 质检无布局/文字/对比度缺陷。

后端测试 **39 passed**；前端 `npm run build` 通过；工作树干净。

## 关键技术决策
- 登录态存于持久 Profile；登录检测=会话列表出现；身份确认=右侧标题严格等值，存疑即 ambiguous 不发。
- 同账号浏览器流程互斥（登录/Dry Run/发送）；`unknown_requires_review` 禁止当日自动重发。
- 发送确认不依赖「气泡出现=成功」，采用「发布按钮触发 + 编辑框清空（零宽感知）+ 气泡内容确认」三重兜底。
- 迁移替换 create_all；真实运行与测试均走 `alembic upgrade head`。
- 不加载任何反检测脚本；验证码与风控仅本人处理。

## 当前核心代码结构
```
backend/app/
  core/        config(database_url/profiles_dir/artifacts_dir)、database、migrations
  models.py    Account/ContractTarget/MessageTemplate/SendJob/SendAttempt + 枚举
  schemas.py   API 输入/输出模型（camelCase 别名字段）
  services.py  账号/模板/好友 CRUD、发送预览、仪表盘聚合
  execution/   profile_store、selectors、douyin_chat、send_confirmer、message_renderer、browser_manager
  api/         accounts、targets、templates、dashboard、execution(login/dry-run/send)、health、router
backend/scripts/  search_debug / check_state / verify_sent / probe_dry_run / probe_send /
                   reset_unknown_jobs / finalize_unknown_jobs（实机排查/后台账工具）
frontend/src/
  App.tsx(顶栏导航+页面切换+全局刷新)、components/DashboardPage/AccountsPage/TargetsPage/
  TemplatesPage/JobsPage/CreateAccountModal/SendModal/StatusBadge
  lib/format.ts(状态文案映射)、api.ts、types.ts、theme.css(Vercel 浅色)
```

## 测试与验证结果
| 项目 | 结果 |
|---|---|
| 后端 pytest | 39 passed（CRUD/迁移/登录/定位/Dry Run/发送终态/幂等/RealApiPage 回归/零宽清空/气泡升级） |
| 前端 npm run build | tsc --noEmit + vite build 通过 |
| 真实发送 E2E | 测试111 送达小号；重复发送正确跳过 |
| 五个页面视觉质检 | 无错位/截断/对比度问题 |

## 已知问题 / 边界
- 发送/定位的 DOM 选择器依赖抖音当前页面结构（`selectors.py`），页面改版可能失效；`SendConfirmer` 在无可靠信号时记 unknown 而非误判成功，是安全设计。
- 真实发送的「终态确认」目前以气泡内容 + 发布按钮为主，`SendStatusretry` 等失败标记选择器未经验证，失败标记可能检测不到（会退化为 unknown 而非误判成功）。
- Profile 数据高敏感，仅依赖系统用户权限，未做额外加密。
- Starlette httpx→httpx2 弃用警告存在，功能无影响。
- 尚无 Scheduler（M4）；SendJob 目前由「立即发送」手动触发写入。

## 下一步开发顺序
1. **M4 调度器**：按 account.timezone + send_window 生成每日任务；数据库租约（lease）+ heartbeat + 账号互斥；在窗口内随机取时；定时进程或 APScheduler（本地单进程即可，暂不引 Celery）。
2. M5 增强：原生表情/图片、通知（Bark/钉钉）、selector 热更、火花天数自动读取校准。
3. 后续：若转多用户/远程，需加鉴权与加密（当前仅监听 127.0.0.1，勿暴露公网）。

## 应急运维工具（backend/scripts，实机排查用）
- `probe_dry_run.py <account_id>`：走产品代码路径跑一次定位校验（只读不发）。
- `probe_send.py <account_id>`：走产品代码路径实际发送一次。
- `check_state.py <account_id> <text>`：区分消息困在编辑器 vs 已进气泡；核对发送按钮选择器。
- `verify_sent.py <account_id> <text>`：全文检索文案是否已出现在会话里。
- `finalize_unknown_jobs.py` / `reset_unknown_jobs.py`：人工核对后核销/重置当日任务状态。
