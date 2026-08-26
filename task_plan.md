# Task Plan: Flame Contractor 本机自用版骨架

## Goal
完成本机自用版 PRD、技术设计、数据库/API 规格，并交付可运行且通过测试的 FastAPI + SQLite + React/Vite 项目骨架。

## Next Step
交付骨架并说明启动方式；下一里程碑为 M2（单账号扫码登录 + Dry Run）。

## Current Phase
Complete

## Phases

### Phase 1: 范围确认与环境检查
- [x] 确认首版为本机自用版
- [x] 确认本轮交付为 PRD + 技术设计 + 可运行骨架
- [x] 核验本机 Python/Node/包管理器/Git
- **Status:** complete

### Phase 2: 产品与技术文档
- [x] 编写 PRD（用户流程、P0、验收标准、非目标）
- [x] 编写技术设计（架构、状态机、安全边界）
- [x] 编写数据库 schema 与 API 契约
- **Status:** complete

### Phase 3: 后端骨架
- [x] 初始化 FastAPI 应用与配置
- [x] 建立 SQLite 模型、初始化和 service 边界
- [x] 实现 health、dashboard、accounts、targets、templates 基础 API
- [x] 添加后端测试（10 项全部通过）
- **Status:** complete

### Phase 4: 前端控制台
- [x] 初始化 React/Vite/TypeScript
- [x] 建立具有 Flame Contractor 视觉身份的应用壳
- [x] 实现仪表盘与账号列表基础联调
- [x] 完成类型检查与生产构建验证
- **Status:** complete

### Phase 5: 集成验证与交接
- [x] 运行后端测试（10 passed）
- [x] 运行前端构建与类型检查（通过）
- [x] 验证本地启动流程和 API（含浏览器端到端交互）
- [x] 更新 README、PROJECT_STATUS 与进度记录
- **Status:** complete

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
