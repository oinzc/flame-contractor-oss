# PROJECT_STATUS — Flame Contractor（焰之契约者）

> 最近更新：2026-08-30 | 功能提交基线至 M8 收尾（见 git log）

## 当前状态
**M5–M8 全部达成**。M6 可靠性闭环（失败截图/网络预检/有界重试/发送冷却/窗口策略/熔断/成功推送/开机自启）、M7 前端「驾驶舱」重设计（D1 蚀刻 bento，用户选定）、M8 体验功能（文案池 LRU、统计+CSV、备份导入导出、截图扫码登录）全部落地。后端 pytest **112 passed**，前端 build 通过，浏览器实测通过。分期与不做清单见 `task_plan.md`。

**M5 剩余项**：无——原生表情发送也已实机验证完成。通知补渠道（钉钉/邮件）经评估决定不扩，保持仅 Bark。

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
- **完整真机 E2E**：到期计划由 `Scheduler.tick()` 派发 1 个账号，`BrowserManager.run_send` 真实发送并确认，最终 `SendSchedule.succeeded` + 对应 `SendJob.succeeded`；再次 `tick()` 返回 `dispatched=0` 且不调用发送执行器。

### M5 图片发送 + 火花校准（HEAD `18d9115`）
- **图片模板**：`TemplateType` 新增 `IMAGE` + `MessageTemplate.image_path`（Alembic 迁移 `a0737c65f237`，仅加列不重建表）。前端模板页支持选文本/图片 + 图片路径。
- **图片发送链路**（参考 unmev/douyin-auto-fire，实机验证）：
  - 当前抖音聊天版发图流程是「点附件图标 `.semi-upload-add` → 唤起 filechooser → 选图 → 弹出「发送给 xx」确认弹窗 → 点弹窗内「发送」」。
  - `send_image` 用 `expect_file_chooser` + `set_files` 注入 + 点确认弹窗发送。
  - **兼容 DOM 改版**：新增 selector `IMAGE_UPLOAD_TRIGGER`/`IMAGE_DIALOG_SEND`。
- **图片终态确认**：`_confirm_send` 图片分支轮询 `image_bubble_confirmed` —— 发送前统计非头像图片数（`_count_non_avatar_images`），发送后要求数量增加才判成功，防历史图片误判。实机验证：图片真实发出且记 succeeded。
- **火花天数手动校准**：`PATCH /api/targets/{id}` 仅改 `streakDays`（`TargetPatch`），参考 ScriptCat"手动调整"；好友列表「校准」按钮。
- **Bark 通知**：`app/notifications.py`，异常推送（登录失效/发送中断/部分未确认）；`FLAME_BARK_URL` 环境变量配置，`trust_env=False` 绕过 SOCKS 代理。
- 前端：DashboardPage「自动调度」卡片 + 模板页图片类型 + 好友页火花校准按钮。

### M5 selector bundle 热更新（HEAD `d687f88`）
- **选择器外置为 JSON bundle**（`app/execution/selectors_bundle.json`），运行时唯一来源，不再硬编码进 `selectors.py`；`selectors.py` 保留同名常量作为默认值兜底（`tests/fakes.py`/`scripts` 的 `from .selectors import X` 无缝继续）。
- **mtime 惰性重载**：`_Bundle` 在访问时检查 JSON 的 mtime，文件一变即自动重读，因此**抖音改版只需编辑 JSON，无需重启后端进程**——即将到达的登录/定位/发送自动用新选择器。
- **运行期取值**：`douyin_chat.py` 统一改为 `from . import selectors as _s` 后以 `_s.X` 使用（而非 import 时捕获常量），这是热更新真正生效的前提。
- **API**：`GET /api/selectors`（当前 bundle 快照）、`POST /api/selectors/reload`（强制重载）。
- **验证**：新增 `tests/test_selectors_bundle.py` 7 条（默认加载/未知 key 报错/显式 reload/惰性 mtime/DouyinChat 运行期取值反映重载/API 读与重载），后端 pytest **73 passed**（69 + 4 sticker）。

### M5 原生表情发送（HEAD `0249a38`）
- **TemplateType 加 `STICKER`**；`MessageTemplate` 加 `sticker_name/label/category/fallback_index`（迁移 `fe2a91c8b3d4`，仅加列）；schema 校验（缺表情名→422）+ 预览 `[表情] {name}`。
- **`douyin_chat.send_sticker`**：点表情按钮 → 打开面板 → 按名称精确匹配表情项（`.emojiEmojiItememojiItem` + 名称节点）→ **一点即发**；`finally` 关闭面板并恢复输入框焦点（unmev issue #4 的面板遮挡教训——否则会挡住后续消息）。
- **真机核对 + 修复**（HEAD `0249a38`）：
  - 真机探针 `scripts/probe_sticker.py`（只定位不发）确认表情按钮 `svg.messageMsgInputiconAction`、面板 `.componentsemojiemojiPanel` 可用，24 个表情项（比心/续火花等）可定位。
  - E2E 暴露两个 Bug 并修复：①面板打开后表情项异步渲染，须先 `_wait_sticker_items` 再遍历（否则 count=0 误报找不到）；②确认须复用图像 `image_bubble_confirmed`（`contentBox+isFromMe+非头像 img` 的 cur>before 锚定），不能用依赖 `messageMessageBoxcontentBox` 的图片专用 `_wait_image_bubble`（表情气泡未必匹配）。
- **终态确认**与图片一致：`_confirm_send` 对 IMAGE/STICKER 都走「从我的 + 非头像」增图锚定，绝不把头像/旧图当成功；确认不了记 `unknown_requires_review`。
- **验证**：`tests/test_sticker.py` 4 条（模板校验/匹配发送成功/找不到表情失败/无气泡不判成功）；**原生表情真实发送 E2E** 通过：oinzc 会话 exact=True，「比心」表情发出，`SEND_STICKER OK` + `last_bubble_seen True` + `IMAGE_BUBBLE_CONFIRMED True`。
- 前端模板页支持「表情」类型 + 表情名称/分类字段。

## 关键技术决策
- 登录态存于持久 Profile；登录检测=会话列表出现；身份确认=右侧标题严格等值，存疑即 ambiguous 不发。
- 同账号浏览器流程互斥（登录/Dry Run/发送）；`unknown_requires_review` 禁止当日自动重发。
- 发送确认不依赖「气泡出现=成功」，采用「发布按钮触发 + 编辑框清空（零宽感知）+ 气泡内容确认」三重兜底。
- **M4 调度粒度 = 账号级随机**（对齐 `docs/TECHNICAL_DESIGN` 的「每账号一个 worker」+「同账号并发=1」+ 调研的「账号窗口随机」）：每账号在窗口内取一个时刻，到点由同一浏览器会话批量发送该账号所有启用好友，而非每好友各自随机时刻。理由：复用已验证链路、改动最小、零迁移风险；好友级随机需多次开浏览器会话，违背项目既定设计且实现更重。
- 调度器只决定「账号今天何时发」，好友级发送与幂等（`run_send` 的 `(account, target, business_date, message_slot_id)`）完全复用。
- SQLite 读出的 datetime 为 naive：调度器内统一用 `_utc_naive` 归一化后再比较，避免 aware/naive 比较错误。
- 迁移替换 create_all；真实运行与测试均走 `alembic upgrade head`。
- **火花天数 = 本地累计（`streak_days` 发送成功 +1），非抖音实时读取**：抖音网页版不提供稳定、对所有好友存在的火花天数 DOM；参考 ScriptCat-Douyin-Fire-Helper（本地记录+每日+1+手动调整）。DOM 读取已实机验证不可靠（误读风险 > 收益），放弃。
- **图片确认 = 发送前后非头像图片数量锚定**：图片气泡出现有延迟，且会话里头像图片会污染「contentBox 有 img」判断。`image_bubble_confirmed` 发送前记非头像图片数，发送后要求数量增加才判成功，规避历史图片误判，绝不把头像/旧图当成功。
- 不加载任何反检测脚本；验证码与风控仅本人处理。

## 当前核心代码结构
```
backend/app/
  core/        config(database_url/profiles_dir/artifacts_dir)、database、migrations
  models.py    Account/ContractTarget/MessageTemplate/SendJob/SendAttempt/SendSchedule + 枚举
  schemas.py   API 输入/输出模型（camelCase 别名字段）+ SendScheduleRead/SchedulerStatusRead
  services.py  账号/模板/好友 CRUD、发送预览、仪表盘聚合
  scheduler.py M4 调度器：懒生成/窗口随机/租约互斥/崩溃恢复/暂停
  execution/   profile_store、selectors（+selectors_bundle.json）、douyin_chat、send_confirmer、message_renderer、browser_manager
  api/         accounts、targets、templates、dashboard、execution、scheduler、selectors、health、router
backend/alembic/versions/  baseline(722cd9dc7d0b)、add_send_schedule(15a5feca9ee1)、add_image_path(a0737c65f237)、add_sticker(fe2a91c8b3d4)
backend/scripts/  search_debug / check_state / verify_sent / probe_dry_run / probe_send /
                   probe_sticker / probe_sticker_send / probe_login_wait / probe_scheduler_e2e /
                   reset_unknown_jobs / finalize_unknown_jobs（实机排查/后台账工具）
frontend/src/
  App.tsx(顶栏导航+页面切换+全局刷新+scheduler 状态)、components/DashboardPage/AccountsPage/
  TargetsPage/TemplatesPage/JobsPage/CreateAccountModal/SendModal/StatusBadge
  lib/format.ts(状态文案映射+调度映射)、api.ts、types.ts、theme.css(Vercel 浅色)
```

## 测试与验证结果
| 项目 | 结果 |
|---|---|
| 后端 pytest | **77 passed**（39=CRUD/迁移/登录/定位/Dry Run/发送终态/幂等/RealApiPage 回归/零宽清空/气泡升级；10=调度；8=通知+图片：图片注入/图片不误判头像/图片模板校验/火花校准/Bark URL/推送无误；7=selector bundle：默认加载/未知 key/显式 reload/惰性 mtime/运行期取值/API 读与重载；4=原生表情：模板校验/匹配发送成功/找不到表情失败/无气泡不判成功；4=登录检测：轮询直到渲染/永不渲染超时/延迟渲染发送成功/仍未渲染判过期） |
| 前端 npm run build | tsc --noEmit + vite build 通过 |
| 端到端启动 | uvicorn 启动后 `/health=database:ready`；`/api/scheduler` 返回 enabled=true、真实账号今日计划（business_date=2026-08-27、scheduled_at 落在账号窗口内、status=pending） |
| 端到端自动调度真机 E2E | 到期计划 → `Scheduler.tick()` 派发 1 个账号 → 真实 `run_send` 发送与终态确认；计划 `succeeded`、任务 `succeeded`；再次 tick `dispatched=0` 且未调用发送执行器 |
| M3 真实发送 E2E | 测试111 送达小号；重复发送正确跳过（M3 保留，未回归） |
| **图片真实发送 E2E** | 绑定图片模板真实发图：图片气泡出现（base64 非头像图），`SEND_RESULT=succeeded`、`IMG_BUBBLE_CONFIRMED=True`；确认逻辑不误判成功 |
| **原生表情真实发送 E2E** | 绑定「比心」表情模板真实发送：oinzc 会话 exact=True，`SEND_STICKER OK`、`last_bubble_seen=True`、`IMAGE_BUBBLE_CONFIRMED=True` |

## 已知问题 / 边界
- 发送/定位的 DOM 选择器依赖抖音当前页面结构（`selectors.py`），页面改版可能失效；`SendConfirmer` 在无可靠信号时记 unknown 而非误判成功，是安全设计。
- 真实发送的「终态确认」以气泡内容 + 发布按钮为主，`SendStatusretry` 等失败标记选择器未经验证，失败标记可能检测不到（会退化为 unknown 而非误判成功）。
- Profile 数据高敏感，仅依赖系统用户权限，未做额外加密。
- Starlette httpx→httpx2 弃用警告存在，功能无影响。
- **调度器为进程内内存循环（APScheduler），未用持久化 jobstore**：进程重启后后台循环自动重跑 `tick`，会重新生成当日/补跑当日计划（幂等键兜底不会重复发送）。真正需要的持久调度语义由 `SendSchedule` 表承载（唯一约束 + 租约字段），重启安全。
- 调度默认每 60s 轮询一次，到点判定依赖 `scheduled_at`；若进程在窗口内长时间挂起，重启后可能补发当日计划（幂等兜底）。
- **headless 下登录检测波动（已修复，HEAD `4aa851b`）**：原 `run_send`/Dry Run 用 headless 打开持久 profile 后立即 `is_logged_in()` 单次查询会话列表，页面加载早期列表未渲染完易返回 False，被误判 auth_expired 并跳过发送。已改为 `DouyinChat.wait_logged_in(timeout, poll_interval)` 轮询：会话列表一旦出现即算已登录、超时才判失效；run_send / run_dry_run / 扫码登录三处统一走它，并新增 `login_check_timeout` 构造参数（默认 8s）。真机验证 `wait_logged_in=True`，截图经视觉桥确认会话列表完整渲染（实机复现偶发单次 False 不总是发生，但轮询逻辑已由单测覆盖「延迟渲染→True / 永不渲染→False」）。
- **selector bundle 依赖 JSON 有效性**：`selectors_bundle.json` 语法错误或字段缺失时，`_Bundle` 会保留内存中的默认/上一版值继续运行（安全方向、不误发），但需人工修正并按需 `POST /api/selectors/reload`（或进程内惰性 mtime）才会刷新。

## 尝试过但失败的方案
- **火花天数 DOM 读取（已放弃）**：尝试从抖音会话页读取真实火花天数，结论是不可靠——火花提示只在会话列表项的特定标签里出现、文案随状态变化（为你恢复 N 天火花 / 重燃中 N/M / 已连续 N 天）、不是每个好友都有（oinzc 实测无提示），且类名被多会话共用、抓整页会串行误读。读错天数发给好友比不读更糟，违背"宁可不发不错发"。采纳参考项目 ScriptCat 的"本地累计 + 手动调整"做法。
- **图片发送旧实现（已替换）**：旧 `send_image` 直接找 `input[type=file]` 注入后点编辑器发布按钮 —— 但当前抖音聊天版发图是「点附件图标 → 选图 → 确认弹窗「发送」」，旧实现导致图发不出。实机验证改用确认弹窗发送后才真正发出。
- M3 期间的实测坑（协议泄漏/Enter 不发送/零宽字符/预热传选择器/迁移 import 路径）见 `task_plan.md`「Errors Encountered」。

## 下一步开发顺序
M6/M7/M8 已全部完成，无强制待办。可选后续（未排期）：
1. 统计页图表可视化（当前为数字块 + CSV）；
2. 多账号实测（架构已支持账号级隔离，但只实测过 1 个账号）；
3. 验证码/风控页检测——等真实风控页样本出现后用失败截图留证再写 selector；
4. 转多用户/远程时才加鉴权与加密。

## 换机与黑屏事件记录（2026-08-29）
- **跨机迁移后登录失效**：抖音识别新设备作废旧会话（预期现象），须重新扫码。
- **有头窗口整页黑屏（已部分修复，`84e9326`）**：两层原因——①Chromium「原生窗口遮挡计算」在自动化窗口上误判被遮挡而停止绘制，已通过 `--disable-features=CalculateNativeWinOcclusion` 修复（全新 Profile 验证有效）；②真实 Profile 下有头模式抖音页面 JS 卡在早期、登录框/会话列表不挂载（未深挖，headless 完全正常）。**全项目仅扫码登录用有头窗口**，发送/定位/调度均 headless 不受影响。
- **本机重新扫码的可行路径（已实测）**：headless 打开登录页 + 定时截图二维码供手机扫描，登录态照常写入持久 Profile。计划在 M8-4 产品化为「截图扫码登录」。
- **运维备忘**：调度器 `paused` 为内存态，重启后端即丢失，需重新暂停/恢复；`account.status` 在库中存枚举名（如 `HEALTHY`）。

## 应急运维工具（backend/scripts，实机排查用）
- `probe_dry_run.py <account_id>`：走产品代码路径跑一次定位校验（只读不发）。
- `probe_send.py <account_id>`：走产品代码路径实际发送一次。
- `check_state.py <account_id> <text>`：区分消息困在编辑器 vs 已进气泡；核对发送按钮选择器。
- `verify_sent.py <account_id> <text>`：全文检索文案是否已出现在会话里。
- `finalize_unknown_jobs.py` / `reset_unknown_jobs.py`：人工核对后核销/重置当日任务状态。
