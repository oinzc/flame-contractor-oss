# PROJECT_STATUS — Flame Contractor（焰之契约者）

> 最近更新：2026-09-11 | 功能基线：M1–M10 + Windows 常驻自恢复（见 git log）

## 当前状态
**M5–M10 全部达成**。平台账号与既有发送/扫描能力已落地；2026-09-11 新增 Windows 常驻自恢复。后端 pytest **205 passed**，前端 build 通过。分期与不做清单见 `task_plan.md`。

**2026-09-11 官方火花数字快照修复**：扫描原文已正确识别，但旧实现只保存特殊文字徽标，好友列表在普通状态下误显示本地累计值（例如本地 7 天而抖音为 105 天）。新增 `ContractTarget.spark_days`/API `sparkDays` 保存最近一次官方数字快照；列表与总览展示官方值，`streak_days` 继续只用于本地累计、发送模板和发送业务。已用 2026-09-11 最近一次 55 条扫描快照补齐现有对象，未重新扫描或发送。

**2026-09-11 Windows 常驻运行**：参考 Microsoft PowerToys Awake、WinSW 和 FastAPI 官方前端托管方案。`scripts/runtime/manage_runtime.ps1` 在当前 Windows 用户下安装 `Flame Contractor Runtime` 计划任务；监督器持有 `ES_SYSTEM_REQUIRED` 电源请求，使显示器可关闭但保持系统执行，并每 15 秒检查 `/health`。后端退出时只重启已确认由监督器启动的 PID。FastAPI 通过 `app.frontend()` 托管 Vite `dist`，常驻 UI/API 统一为 `http://127.0.0.1:8000/`，不再常驻 Vite dev server。日志位于 `%LOCALAPPDATA%\FlameContractor\logs`。安装前已把调度持久化为暂停，重启验证没有触发 Dry Run 或真实发送。

**2026-09-11 扫描熄火复核**：完整手动扫描仍自动刷新已存在对象、只让新会话由用户勾选导入。已存在的普通好友若本次明确扫描到但没有官方火花，列表显示红色“无火花”而不是回退成仍有效的本地累计天数；扫描结果新增“熄火复核”，列出本地历史天数并由用户选择“暂时保留”或二次确认后批量软删除。删除会停止后续续火但保留历史任务，再次扫描导入可恢复。群聊、未完成扫描和“本次未观察到”的对象不进入该提醒，也不会自动删除或暂停。

**2026-09-10 平台账号完善**：全新数据库不再自动暴露默认账号，登录页会先读取 `GET /api/auth/setup`，仅在用户表为空时展示一次性管理员初始化；完成后 `POST /api/auth/register` 关闭，新增成员统一由管理员通过 `POST /api/auth/users` 创建。新建/修改/重置密码至少 8 位，后端禁止取消最后一名管理员。前端登录页按 D1「驾驶舱」蚀刻语言重做，移除登录/注册切换、默认凭据提示和无法真正完成的自助找回按钮；管理员页补建号表单，窄屏用户表改为卡片布局。后端认证提交为 `2e8c822`；最终测试与前端提交见最新 git log。

**账号功能验证**：后端认证定向测试 20 项和全量测试通过；前端 `npm.cmd run build` 通过。真实 `127.0.0.1:5173` 已检查 1440×900 与 390×844 登录页、密码显示切换、管理员建号密码不一致拦截和平台用户窄屏布局；隔离空数据库实例已检查首次初始化页面。验证未创建用户、未改密码、未触发任何抖音发送。内置浏览器控制接口连续连接失败，按仓库约定降级使用本地 Playwright，控制台无 error/warning。

**2026-08-31 全局调度暂停状态持久化**：新增 `AppSetting` 与 Alembic 迁移 `b7e3d1f9a2c4`；`POST /api/scheduler/pause|resume` 会写入 `scheduler.paused`，后端启动时恢复，重启后不会意外恢复自动发送。

**2026-09-02 会话状态自动同步**：Scheduler 默认每 24 小时触发一次会话列表后台扫描，服务启动后首次等待约 1 分钟，并避开发送窗口开始前 15 分钟至窗口结束；完成后只刷新已存在好友/群聊的官方火花状态，不自动导入新会话、不发送消息。总览可在 1–168 小时内设置周期并写入 `AppSetting`，环境变量 `FLAME_AUTO_SCAN_INTERVAL_HOURS` 仅作为无持久化设置时的初始默认值；`GET /api/scheduler` 返回周期、最近同步时间、下一次同步时间和运行状态，`PATCH /api/scheduler/settings` 更新周期。自动同步运行时由前端独立显示 `AUTO SYNC` 全局任务坞，持续展示真实处理数、当前会话和百分比；切换页面或收起面板不影响后台任务，完成后保留只读结果摘要。全局调度暂停时自动同步不运行。

**2026-09-02 手动强制发送**：账号卡片新增「强制发送全部」入口，调用 `POST /api/accounts/{id}/send?force=true`，只处理全部启用好友/群聊，即使当天同一模板版本已有成功记录也会继续发送，并为每次强制运行创建带 `:force:` 的独立 `message_slot_id` 审计槽位。该模式只能由手动入口触发；身份存疑、结果未知、账号暂停、登录失效或网络不可用仍然跳过/阻止发送，且未知结果会跨普通与强制槽位阻止当天再次发送。普通发送和自动调度会识别同模板版本的强制成功并保持日幂等，不因强制入口产生自动重复。

**2026-09-03 单好友强制发送 + 总览计数修正**：
- 好友列表新增每行「发送」入口，打开 `SendTargetModal`：可选择模板，也可直接输入文本（二选一，`POST /api/accounts/{id}/targets/{targetId}/send`）。仅绕过当天成功记录并创建独立 `:force:` 审计槽位；身份、登录、网络、`unknown_requires_review` 等安全边界全部保持。直接输入文本时 `SendJob` 不引用模板（`SendJob.template_id` 改为可空，Alembic 迁移 `d9f3e5a7b1c2`），槽位以内容哈希命名（`raw:<sha1[:12]>:force:<runId>`）避免超长。
- 总览「今日已发送」由「成功发送条数」改为「**已发送成功去重好友数**」（`COUNT(DISTINCT target_id)`）：同一好友当天多次强制发送只计 1 个已发好友，全部发完即显示好友总数，再发不再增长。

**2026-09-02 交接运行态（已于 2026-09-10 解除）**：当时 8000 仍是旧后端且调度未暂停，因此没有重启。2026-09-10 已先确认 `app_setting.scheduler.paused=1`，再核对并只重启本项目后端；当前 `/health` 为 `database=ready`，账号认证新接口已进入运行态。此次账号页面验证没有执行 Dry Run 或真实发送。

**2026-08-31 会话扫描后台进度与全局任务坞**：新增 `POST /api/accounts/{id}/scan-chats/start` 与 `GET /api/accounts/{id}/scan-chats/progress`；扫描由后端后台任务执行，前端在 App 层轮询，切换总览/任务/账号/模板页不会中断。滚动到底前总数未知，只展示真实的「已发现 N 个会话」与读取中状态；完成后显示 100% 和结果摘要，自动同步已在列表好友的官方火花状态。旧 `POST /scan-chats` 同步接口保留兼容；自动同步使用同一进度接口但以 `source=automatic` 独立呈现，不进入手动导入、取消或重试流程。

**2026-09-01 会话扫描任务可靠性闭环**：新增 `ScanTask`/迁移 `d2e6f8a0b1c3`，每次进度快照和已发现会话条目写入 SQLite；`GET /scan-chats/latest` 可在刷新或后端重启后恢复最近快照。前端任务坞统一承载发送与扫描任务，扫描结果实时增长，支持取消并保留已发现条目、失败重试；后端重启会把遗留 `running` 收束为 `interrupted`，不自动续跑或自动导入/发送，避免重复操作。

**2026-09-01 稳定身份展示与扫描导入修复**：新增 `ContractTarget.platform_handle`（迁移 `e7f9a1c3d5b7`）保存 `unique_id/short_id` 等可读平台号；内部 `platform_user_id` 继续只作为稳定身份核对键，不再直接当作用户可读的「抖音号」。扫描会话名改为读取搜索结果的首个非空昵称，避免把 `(9)` 等官方数量后缀带入严格匹配；扫描结果和导入摘要新增 `bound/not_observed/ambiguous` 身份诊断。未观察到身份数据或同名冲突时明确显示并保持空绑定，绝不猜测。真实账号扫描 44/44 完成，结果页可见 100%、可读平台号与未识别原因。

**2026-09-01 群会话身份补齐**：新增 `ContractTarget.platform_conversation_id`、`conversation_type` 与迁移 `f4a8c2e6b1d3`。扫描监听抖音 `imapi.douyin.com/v1/message/get_message_by_init` 的 protobuf，只提取并交叉校验 field 1/2 的会话 ID、field 3 的群类型和 field 5 的群名，不保存完整响应。真实 Profile 的本次扫描捕获到 10 个群会话（含「老龙帮」「保时捷车友会」「一帅比&俩二臂」等）；批量导入按会话 ID 刷新/改名并防重复，发送前按群会话 ID 二次核验；同名群聊或未观察到 ID 时保持未绑定，宁可不发不错发。

**2026-09-01 普通好友身份补采**：新增 `IdentityHydrationTask` 与迁移 `a7b8c9d0e1f2`。好友页仅对已有且未绑定的普通好友提供「补采好友身份」入口；后端逐个打开精确会话，等待该次打开触发的官方用户信息响应，只在昵称/备注唯一匹配且拿到可靠 ID 时绑定 `platform_user_id`/`platform_handle`。不点击群聊、不发送消息、不使用历史缓存猜测；未观察到、冲突或重复 ID 均保持未绑定并留下结果。任务进度、结果和重启收束状态写入 SQLite，前端使用全局任务坞跨页面展示。

**2026-09-01 发送终态确认修复**：群聊右侧标题如「老龙帮(9)」时只剥离明确的数字未读后缀，仍按主标题严格等值和已保存群会话 ID核对；会话切换后的编辑器等待可见节点完成异步渲染。图片/原生表情发送前后改比较「从我的」媒体气泡签名，兼容虚拟列表回收旧节点导致总图片数不变；表情点击后仅在明确存在且可用的发送按钮时兜底，不再无条件按 Enter。已触发但无法确认的结果统一进入 `unknown_requires_review`，禁止自动重试，避免重复发送。

**发送修复验证**：后端 `backend/tests` 全量 **161 passed**；只读真机核验中，`dad` 会话媒体快照可正常读取，群标题「老龙帮(9)\n861」仍能精确打开并取得群会话 ID。2026-09-02 重启后端并完成 Dry Run：15 个目标全部 `found`，其中 14 个启用目标满足严格定位条件，未发送任何消息；健康接口返回 `database=ready`，`/api/scheduler` 返回 `paused=true`，新代码已进入运行态。

**2026-08-31 发送定位修复**：抖音右侧标题会在主昵称后追加官方火花状态；确认逻辑现在只对首个非空行做严格等值匹配，保留完整标题用于诊断，不放宽为模糊匹配。真实 Dry Run 14 个对象中 10 个 `found`、4 个 `not_found`，未发送消息。

**2026-08-30 CRUD 补齐与好友导入（对照参考项目缺口清零）**：
- 好友启停（`b1dc3dc`）、模板编辑/删除/启停（`af5d43e`，校验规则统一、池候选替换继承 LRU、仅内容变化 bump version）、账号删除+改名（`c525a55`，调度行级联清除 `4ff5616`）
- unknown 任务人工处理闭环（`7d652e8`）：任务页「确认已发送/放行重试」，SendAttempt 审计留痕
- 好友批量导入（`055f512`）→ **扫描会话列表勾选导入**（`b8a00fd`，对齐 ScriptCat「好友批选」）
- **抖音官方火花 DOM 识别续火对象**（`88346c6` + `7a75f28`）：扫描读取名字旁火花徽标（`.commonStreakstreakContainer`），支持三种形态——正常天数（🔥N，normal/couple/family 三色）、**重燃中 X/3**、**N 天后消失**，以及「N天后火花将消失」消息提示兜底；**默认只显示续火对象**，导入自动回填官方天数
- 官方火花状态快照已持久化（迁移 `b8c4d2e6f901`）：总览/好友页按抖音原文展示「重燃中 X/3」「N 天后消失」等徽标；重新勾选已有好友可刷新快照，不改写本地累计天数
- 好友列表默认隐藏暂停项（`41a65f5`）、批量区常显仅保留扫描入口（`4457985`/`226ac50`）
- 真机修复两个基础 bug：调度行误拦账号删除（`4ff5616`）、前端 204 无 body 解析崩溃（`72a42c7`）
- 端到端脚本 `scripts/e2e_crud_test.py`（隔离实例 29 项断言）；真机 43 会话识别 16 续火对象与用户指认一致

**剩余未实现（均有明确结论）**：验证码/风控页自动识别（无真实风控样本，写 selector 即瞎猜；等 M6-1 失败截图留到真实样本后再做）；Docker/Trace/多 worker/钉钉邮件（不做清单）。

**M5 剩余项**：无——原生表情发送也已实机验证完成。通知补渠道（钉钉/邮件）经评估决定不扩，保持仅 Bark。

**火花天数说明（2026-08-30 修订）**：早期结论「网页版无火花 DOM」**已被推翻**——会话列表名字旁有官方火花徽标（`.commonStreakstreakContainer > .commonStreaknormalText`），扫描已接入（见上）。产品内 `streak_days` 仍以本地累计（发送成功 +1）为准、扫描导入新好友时用官方天数初始化；已有好友刷新扫描只更新官方状态快照，不覆盖本地累计值。官方页只提供天级文字时，界面展示原文和扫描时间，不推算小时倒计时；发送链路的「宁可不发不错发」边界不变。

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
- `app/scheduler.py`：懒生成当日计划（窗口内随机取时）、到期调度、账号级租约互斥、崩溃恢复、失败分类、可控暂停（状态持久化）。
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

### M9+ 手动强制发送（2026-09-02）
- 账号页新增「强制发送全部」手动入口与专用确认文案；预览明确显示本次会处理全部启用好友/群聊。
- `POST /api/accounts/{id}/send?force=true` 只绕过当天同账号/好友/业务日期/当前模板版本的成功记录，不绕过严格身份核验、暂停/登录/网络检查或 `unknown_requires_review` 安全闸门。
- 强制运行使用独立 `message_slot_id`（`:force:<runId>`）落库并进入全局任务坞；重复点击强制入口仍按用户明确操作再次处理，普通发送/自动调度会识别强制成功并跳过，避免无意重复。
- 新增后端回归覆盖：强制发送忽略当天成功、未知结果跨槽位阻止、强制成功后普通发送跳过。

## 关键技术决策
- 登录态存于持久 Profile；登录检测=会话列表出现；身份确认=右侧标题主昵称首个非空行严格等值，存疑即 ambiguous 不发。
- 同账号浏览器流程互斥（登录/Dry Run/发送）；`unknown_requires_review` 不论普通还是强制任务槽位都禁止当日自动重发。
- **强制发送是显式手动例外而非自动策略**：只绕过当前模板版本的成功幂等记录，强制任务独立审计；普通发送与自动调度感知强制成功，避免同日自动重复。
- 发送确认不依赖「气泡出现=成功」，采用「发布按钮触发 + 编辑框清空（零宽感知）+ 气泡内容确认」三重兜底。
- **M4 调度粒度 = 账号级随机**（对齐 `docs/TECHNICAL_DESIGN` 的「每账号一个 worker」+「同账号并发=1」+ 调研的「账号窗口随机」）：每账号在窗口内取一个时刻，到点由同一浏览器会话批量发送该账号所有启用好友，而非每好友各自随机时刻。理由：复用已验证链路、改动最小、零迁移风险；好友级随机需多次开浏览器会话，违背项目既定设计且实现更重。
- 调度器只决定「账号今天何时发」，好友级发送与幂等（`run_send` 的 `(account, target, business_date, message_slot_id)`）完全复用。
- SQLite 读出的 datetime 为 naive：调度器内统一用 `_utc_naive` 归一化后再比较，避免 aware/naive 比较错误。
- 迁移替换 create_all；真实运行与测试均走 `alembic upgrade head`。
- **火花天数 = 本地累计（`streak_days` 发送成功 +1）**：会话扫描阶段读取官方火花 DOM 只用于初始化/展示，之后不依赖 DOM 天数做发送决策；参考 ScriptCat-Douyin-Fire-Helper 的本地记录 + 每日 +1 + 手动调整做法。
- **媒体确认 = 发送前后非头像媒体气泡签名锚定**：图片/表情气泡出现有延迟，且会话里头像图片会污染 `contentBox` 判断；抖音虚拟列表还可能回收旧节点使总图片数不变。`image_bubble_confirmed` 优先比较「从我的」气泡的时间、非头像图片稳定路径和气泡文本签名，无法取签名时才回退到发送前后数量，绝不把头像/旧图当成功。
- **全局自动调度开关持久化**：`AppSetting` 以 `scheduler.paused` 记录暂停状态；启动先完成 Alembic 迁移再恢复，恢复失败时保持安全方向，不让暂停配置静默丢失。
- 不加载任何反检测脚本；验证码与风控仅本人处理。

## 当前核心代码结构
```
backend/app/
  core/        config(database_url/profiles_dir/artifacts_dir)、database、migrations
  models.py    Account/AppSetting/ContractTarget/MessageTemplate/SendJob/SendAttempt/SendSchedule/ScanTask + 枚举
  schemas.py   API 输入/输出模型（camelCase 别名字段）+ SendScheduleRead/SchedulerStatusRead
  services.py  账号/模板/好友 CRUD、发送预览、仪表盘聚合
  scheduler.py M4/M9 调度器：发送调度 + 会话自动同步/窗口保护/租约互斥/崩溃恢复/暂停
  execution/   profile_store、selectors（+selectors_bundle.json）、douyin_chat、send_confirmer、message_renderer、browser_manager
  api/         accounts、targets、templates、dashboard、execution、scheduler、selectors、health、router
backend/alembic/versions/  baseline(722cd9dc7d0b)、add_send_schedule(15a5feca9ee1)、add_image_path(a0737c65f237)、add_sticker(fe2a91c8b3d4)、add_app_setting(b7e3d1f9a2c4)、add_spark_snapshot(b8c4d2e6f901)、add_scan_task(d2e6f8a0b1c3)、add_platform_handle(e7f9a1c3d5b7)、add_conversation_identity(f4a8c2e6b1d3)、add_scan_task_source(c6e7f8a9b0d1)
backend/scripts/  search_debug / check_state / verify_sent / probe_dry_run / probe_send /
                   probe_sticker / probe_sticker_send / probe_login_wait / probe_scheduler_e2e /
                   reset_unknown_jobs / finalize_unknown_jobs（实机排查/后台账工具）
frontend/src/
  App.tsx(顶栏导航+页面切换+全局刷新+scheduler 状态)、components/DashboardPage/AccountsPage/
  TargetsPage/TemplatesPage/JobsPage/CreateAccountModal/SendModal/TaskDock/StatusBadge
  lib/format.ts(状态文案映射+调度映射)、api.ts、types.ts、theme.css(D1 蚀刻 bento + 任务坞)
```

## 测试与验证结果
| 项目 | 结果 |
|---|---|
| 后端 pytest | **205 passed**（含一次性管理员初始化、平台账号保护、FastAPI 同进程前端托管和扫描熄火判定；保留 2 条既有 warning：Starlette/httpx 弃用提示、模板池删除行数提示） |
| 前端 npm run build | tsc --noEmit + vite build 通过 |
| 端到端启动 | uvicorn 重启后 `/health=database:ready`；`/api/scheduler` 返回自动同步周期、账号状态（含运行中任务 `taskId`）和原有发送计划字段 |
| 前端真实渲染 | `http://localhost:5173/`，内置浏览器独立标签页完成硬刷新；总览显示可配置“自动同步周期”、只读说明和调度状态，宽度检查无横向溢出，当前刷新标签页控制台无错误 |
| 熄火复核渲染 | 内置浏览器控制接口失败后降级本地 Playwright；隔离数据在 1440×900、390×844 显示“无火花”与删除提醒，控制台无 error/warning；“暂时保留”、刷新后再次提醒、二次确认软删除均通过 |
| **当前运行态（2026-09-10）** | `/health` 返回 `status=ok`、`database=ready`；8000 已加载账号认证新接口；调度持久化为暂停；5173 运行新登录页 |
| 2026-09-01 运行态检查 | 后端 `/health` 返回 `status=ok`、`database=ready`；账号「示例账号」为 `healthy`；前后端均监听回环地址 `127.0.0.1`（8000/5173） |
| 真实会话扫描 | 任务坞实时计数从 `0` 增长到 `39`/`44`；后续本次重启后真实扫描 `54/54` 完成，导入摘要包含 10 个群会话 ID/名称，现有 4 个群聊已按会话 ID 回填好友记录。会话数随抖音页面状态变化，不写死为固定总数 |
| 真实 Dry Run（2026-08-31） | `overall=ok`；14 个对象中 10 个 `found`（含三种火花徽标后缀），4 个 `not_found`；只读，未发送消息 |
| 端到端自动调度真机 E2E | 到期计划 → `Scheduler.tick()` 派发 1 个账号 → 真实 `run_send` 发送与终态确认；计划 `succeeded`、任务 `succeeded`；再次 tick `dispatched=0` 且未调用发送执行器 |
| M3 真实发送 E2E | 测试111 送达小号；重复发送正确跳过（M3 保留，未回归） |
| **图片真实发送 E2E** | 绑定图片模板真实发图：图片气泡出现（base64 非头像图），`SEND_RESULT=succeeded`、`IMG_BUBBLE_CONFIRMED=True`；确认逻辑不误判成功 |
| **原生表情真实发送 E2E** | 绑定「比心」表情模板真实发送：oinzc 会话 exact=True，`SEND_STICKER OK`、`last_bubble_seen=True`、`IMAGE_BUBBLE_CONFIRMED=True` |

## 已知问题 / 边界
- 发送/定位的 DOM 选择器依赖抖音当前页面结构（`selectors.py`），页面改版可能失效；`SendConfirmer` 在无可靠信号时记 unknown 而非误判成功，是安全设计。
- 真实发送的「终态确认」以气泡内容 + 发布按钮为主，`SendStatusretry` 等失败标记选择器未经验证，失败标记可能检测不到（会退化为 unknown 而非误判成功）。
- Profile 数据高敏感，仅依赖系统用户权限，未做额外加密。
- Starlette httpx→httpx2 弃用警告存在，功能无影响。
- **调度器为进程内内存循环（APScheduler），未用持久化 jobstore**：进程重启后后台循环自动重跑 `tick`，会重新生成当日/补跑当日计划（幂等键兜底不会重复发送）。真正需要的计划语义由 `SendSchedule` 表承载（唯一约束 + 租约字段），全局暂停开关另由 `AppSetting` 持久化并在启动恢复。
- 调度默认每 60s 轮询一次，到点判定依赖 `scheduled_at`；若进程在窗口内长时间挂起，重启后可能补发当日计划（幂等兜底）。
- **headless 下登录检测波动（已修复，HEAD `4aa851b`）**：原 `run_send`/Dry Run 用 headless 打开持久 profile 后立即 `is_logged_in()` 单次查询会话列表，页面加载早期列表未渲染完易返回 False，被误判 auth_expired 并跳过发送。已改为 `DouyinChat.wait_logged_in(timeout, poll_interval)` 轮询：会话列表一旦出现即算已登录、超时才判失效；run_send / run_dry_run / 扫码登录三处统一走它，并新增 `login_check_timeout` 构造参数（默认 8s）。真机验证 `wait_logged_in=True`，截图经视觉桥确认会话列表完整渲染（实机复现偶发单次 False 不总是发生，但轮询逻辑已由单测覆盖「延迟渲染→True / 永不渲染→False」）。
- **selector bundle 依赖 JSON 有效性**：`selectors_bundle.json` 语法错误或字段缺失时，`_Bundle` 会保留内存中的默认/上一版值继续运行（安全方向、不误发），但需人工修正并按需 `POST /api/selectors/reload`（或进程内惰性 mtime）才会刷新。
- **会话扫描不是可跨重启续跑的队列**：应用内切换页面或关闭前端面板不会影响后台扫描；每次进度快照与已发现条目持久化到 SQLite。后端重启会将遗留 `running` 收束为 `interrupted`，保留部分结果但不自动续跑；`completed`/`cancelled` 快照可从 `latest` 读取。扫描总数在滚动到底前未知，界面不会用滚动轮次伪造百分比。
- **群会话身份依赖抖音初始化 protobuf 的当前字段形状**：当前按 field 1/2/3/5 交叉校验提取 ID/群类型/名称；字段缺失、页面改版或同名群聊时保守显示未识别/冲突，不猜测绑定，也不会因此发送。
- **普通好友身份补采可能改变已读状态**：为触发官方用户信息响应，补采会打开已有好友会话；任务只做身份读取，不发送消息。若页面未触发可靠响应，任务会记录 `not_observed` 并保持未绑定；此边界由好友页确认提示和后端结果状态共同约束。
- **真实发送仍需逐次确认**：当前 8000 已是新进程且调度暂停，但本轮只验证平台登录与账号管理，没有执行 Dry Run 或真实发送。任何真实发送仍必须先 Dry Run，再取得维护者明确确认。

## 尝试过但失败的方案
- **早期全量直读火花 DOM（已放弃）**：曾尝试把会话页火花 DOM 直接作为发送依据，但提示文案随状态变化、不是每个好友都有且类名会串读。当前仅在扫描阶段识别官方徽标用于初始化/展示，发送决策仍坚持本地累计 + 手动调整，避免读错天数。
- **图片发送旧实现（已替换）**：旧 `send_image` 直接找 `input[type=file]` 注入后点编辑器发布按钮 —— 但当前抖音聊天版发图是「点附件图标 → 选图 → 确认弹窗「发送」」，旧实现导致图发不出。实机验证改用确认弹窗发送后才真正发出。
- **原生表情确认旧实现（已替换）**：旧逻辑在气泡确认超时后无条件按 Enter，可能把已经发出但因虚拟列表回收而未被数量检测到的表情再次发送；现改为媒体气泡签名确认，无法确认时进入 `unknown_requires_review` 并阻止自动重试。
- **2026-09-02 自动重启曾被安全阻断（已处理）**：当时调度未暂停，因此保留旧进程。2026-09-10 已在持久化暂停状态下核对进程归属并安全重启。
- M3 期间的实测坑（协议泄漏/Enter 不发送/零宽字符/预热传选择器/迁移 import 路径）见 `task_plan.md`「Errors Encountered」。

## 下一步开发顺序
1. 保持调度暂停，完成一次 `Win+L` 锁屏跨夜验证；第二天检查计划任务、PID、`/health` 和监督器日志。
2. 验收后把常驻运行的纯代码提交 cherry-pick 到 `main`，不合并 `migration-data-20260904` 里的运行数据。
3. 发送链路后续验证仍先 Dry Run；只有维护者明确确认后才允许真实发送。

## 换机与黑屏事件记录（2026-08-29）
- **跨机迁移后登录失效**：抖音识别新设备作废旧会话（预期现象），须重新扫码。
- **有头窗口整页黑屏（已部分修复，`84e9326`）**：两层原因——①Chromium「原生窗口遮挡计算」在自动化窗口上误判被遮挡而停止绘制，已通过 `--disable-features=CalculateNativeWinOcclusion` 修复（全新 Profile 验证有效）；②真实 Profile 下有头模式抖音页面 JS 卡在早期、登录框/会话列表不挂载（未深挖，headless 完全正常）。**全项目仅扫码登录用有头窗口**，发送/定位/调度均 headless 不受影响。
- **本机重新扫码的可行路径（已实测）**：headless 打开登录页 + 定时截图二维码供手机扫描，登录态照常写入持久 Profile。计划在 M8-4 产品化为「截图扫码登录」。
- **运维备忘**：调度循环本身为进程内状态，重启后会重新轮询；全局调度 `paused` 已由 `app_setting.scheduler.paused` 持久化并在启动恢复；`account.status` 在库中存枚举名（如 `HEALTHY`）。

## 应急运维工具（backend/scripts，实机排查用）
- `probe_dry_run.py <account_id>`：走产品代码路径跑一次定位校验（只读不发）。
- `probe_send.py <account_id>`：走产品代码路径实际发送一次。
- `check_state.py <account_id> <text>`：区分消息困在编辑器 vs 已进气泡；核对发送按钮选择器。
- `verify_sent.py <account_id> <text>`：全文检索文案是否已出现在会话里。
- `finalize_unknown_jobs.py` / `reset_unknown_jobs.py`：人工核对后核销/重置当日任务状态。
