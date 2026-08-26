# Task Plan: Flame Contractor M2 — 扫码登录与 Dry Run

## Goal
完成 M2：Alembic 基线迁移、每账号独立浏览器 Profile、扫码登录流程、Dry Run 流程，配套 API、测试与前端交互。

## Next Step
等待用户在本机执行真实扫码验证（唯一人工环节）。

## Current Phase
Complete

## Phases

### Phase 1: 迁移与依赖
- [x] 提交骨架基线（9d535c4）
- [x] 引入 Alembic，生成并验证基线迁移
- [x] 引入 Playwright 并安装 Chromium
- **Status:** complete

### Phase 2: 执行层
- [x] 实现每账号 Profile 存储
- [x] 实现抖音聊天适配层与选择器集中管理
- [x] 实现扫码登录流程管理器（状态机、注册表、超时）
- [x] 实现 Dry Run 流程（严格匹配、绝不发送、artifact 落盘）
- **Status:** complete

### Phase 3: API 与测试
- [x] 新增 login/status/cancel 与 dry-run 接口
- [x] 注入假浏览器工厂的单元测试（状态机、冲突、认证过期）
- [x] 迁移测试与回归测试全部通过（16 passed）
- **Status:** complete

### Phase 4: 前端
- [x] 账号卡片接入扫码登录按钮与轮询状态
- [x] 账号卡片接入 Dry Run 与结果展示
- [x] 类型检查与生产构建通过
- **Status:** complete

### Phase 5: 交接
- [x] 更新 API 文档、README 与 PROJECT_STATUS
- [x] 说明用户扫码验证步骤（真实 E2E 唯一人工环节）
- **Status:** complete

## Success Criteria
1. ✅ 启动即执行 Alembic 迁移，空库自动建表。
2. ✅ 每账号 Profile 目录独立，路径不可由用户输入。
3. ✅ 登录流程 headed 浏览器 + 等待扫码 + 成功/过期/失败状态可查询。
4. ✅ Dry Run 只定位不发送，身份存疑记为 ambiguous，登录失效标记 auth_expired。
5. ✅ 测试用假浏览器工厂通过，不依赖真实抖音网络。
6. ✅ 前端可发起扫码登录并轮询状态，可发起 Dry Run 并展示逐目标结果。
7. ✅ 全部自动化检查通过；真实 E2E 由用户扫码完成。

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 用 launch_persistent_context 持久 Profile | 登录态落在浏览器 Profile 内，无需手工粘贴 Cookie |
| 登录检测以会话列表出现为准 | 与参考项目一致的稳定信号，避免仅凭 Cookie 判断 |
| 不添加任何反检测脚本 | 与安全边界一致，不规避平台保护 |
| 迁移替换 create_all | 进入有真实部署的阶段，schema 演进必须有版本 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| autogenerate 连接默认库失败（data/ 不存在） | 1 | env.py 在线迁移前创建 SQLite 父目录 |
| 基线迁移 NameError: sqlmodel 未导入 | 1 | 迁移文件与 env.py 显式 import sqlmodel |
| alembic path_separator 弃用警告 | 1 | alembic.ini 增加 path_separator=os |

## Success Criteria
1. `backend` 可以启动，`/api/health` 返回健康状态。
2. SQLite 首次启动自动建立核心表，并有数据库唯一约束承载幂等设计。
3. 账户与契约对象 API 至少具备列表/创建的安全骨架，不接受 Cookie/密码字段。
4. `frontend` 可以构建，并能通过 API 展示仪表盘与账号状态。
5. 文档明确区分已实现骨架与尚未实现的真实抖音自动化。
6. 所有自动化测试与构建命令通过。

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 首版为本机自用 | 凭据留在本机，先验证可靠性，降低 SaaS 合规和安全复杂度 |
| FastAPI + SQLite + React/Vite | 与研究结论一致，依赖成熟，适合单机 MVP |
| 本轮不接真实抖音账号 | 先建立安全数据/API/界面边界，避免凭据和 E2E 阻塞骨架交付 |
| 不加入 Redis/Celery/微服务 | 当前规模不需要，SQLite 任务表和单调度器足够 |
| 不复制 AGPL 项目代码 | 首选借鉴 MIT 项目设计；许可边界清晰 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| 旧调研阶段两个后台 subagent 未产出 | 1 | 已改为主会话逐仓库核验并完成调研 |
| health 接口 NameError（get_settings 未导入） | 1 | 改为从 request.app.state.settings 读取 |
| Windows 缺 IANA 时区数据导致 Asia/Shanghai 校验失败 | 1 | requirements 增加 tzdata |
| JobRead.business_date 声明为 str，date 对象校验失败 | 1 | 改为 date 类型 |
| SQLite 首次启动 unable to open database file | 1 | create_db_and_tables 前自动创建父目录 |
| 后端 uvicorn 起于 127.0.0.1，浏览器经 localhost 访问 Vite 正常 | 1 | Vite 监听 localhost，代理目标 127.0.0.1 工作正常 |

## Notes
- 本阶段不得收集或写入抖音 Cookie、密码、Token。
- “可运行骨架”不等于“自动续火已可用”，最终文档必须明确这一边界。
- 任何外部依赖版本选择都以当前官方文档或本机可用版本为准。
