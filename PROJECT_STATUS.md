# PROJECT_STATUS — Flame Contractor（焰之契约者）

> 最近更新：2026-08-26 | 由 DeepSeek Harness Agent 更新

## 当前目标
M2 已完成：扫码登录 + 独立 Profile + Dry Run。下一步是用户在真实环境扫码验证，然后进入 M3（发送与终态确认）。

## 已完成内容
- 骨架基线已提交（`9d535c4`），M2 变更待提交。
- Alembic 接入：基线迁移 `722cd9dc7d0b`，启动即自动迁移，空库自动建表（已端到端验证 6 张表 + alembic_version）。
- Playwright + Chromium 已装入后端 venv。
- 执行层：
  - `app/execution/profile_store.py`：每账号独立 Profile 目录，ID 白名单校验防路径逃逸；
  - `app/execution/selectors.py`：抖音网页版 DOM 选择器集中管理（注明参考来源，无指纹伪装）；
  - `app/execution/douyin_chat.py`：页面窄封装（登录检测、会话定位、右侧标题二次核对）；
  - `app/execution/browser_manager.py`：扫码登录流程（headed、180s 超时、状态机）+ Dry Run（headless、严格匹配、artifact 落盘、绝不发送）。
- API：`POST/GET /api/accounts/{id}/login`、`POST .../login/cancel`、`POST .../dry-run`。
- 前端：账号卡片新增「扫码登录」按钮（2s 轮询状态、取消）、「Dry Run」按钮与逐目标结果展示（已定位/未找到/身份存疑）。
- 测试：16/16 通过（10 项骨架回归 + 6 项假浏览器执行层测试，不依赖真实抖音网络）。
- 前端类型检查与生产构建通过。

## 关键技术决策
- 登录态存于持久浏览器 Profile（`launch_persistent_context`），无需用户粘贴 Cookie。
- 登录检测 = 会话列表出现；Dry Run 打开会话后必须右侧标题严格等于目标名，否则记 ambiguous。
- 同一账号同一时间只允许一个活动浏览器流程（登录中禁止 Dry Run，反之亦然）。
- 登录成功自动把账号标记 healthy；Dry Run 登录失效自动标记 auth_expired；Dry Run 不改变健康状态。
- 迁移替换 create_all；真实运行与测试均走 `alembic upgrade head`。
- 浏览器操作不加载任何反检测脚本；验证码与风控仅本人处理。

## 修改过的核心文件
- `backend/alembic.ini`、`backend/alembic/env.py`、`backend/alembic/versions/722cd9dc7d0b_baseline.py`、`backend/alembic/script.py.mako`
- `backend/app/core/migrations.py`、`core/config.py`（profiles_dir/artifacts_dir）、`core/database.py`、`main.py`
- `backend/app/execution/`：profile_store、selectors、douyin_chat、browser_manager
- `backend/app/schemas.py`、`api/execution.py`、`api/router.py`
- `backend/tests/test_execution.py`、`tests/conftest.py`
- `frontend/src/types.ts`、`api.ts`、`components/AccountRail.tsx`、`theme.css`、`App.tsx`
- `docs/API.md`、`README.md`、`PROJECT_STATUS.md`、`task_plan.md`、`progress.md`

## 测试与验证结果
| 项目 | 结果 |
|---|---|
| 后端 pytest | 16 passed（迁移、API 回归、登录状态机、冲突 409、Dry Run found/ambiguous/not_found、auth_expired） |
| 前端 npm run build | tsc --noEmit + vite build 通过 |
| 真实启动冒烟 | 健康检查 ok；Alembic 基线迁移在全新库上执行，表齐全 |
| 真实扫码 E2E | 未执行（需用户本人扫码，属预期人工环节） |

## 已知问题
- 真实登录成功判定依赖「会话列表出现」启发式，尚未经用户真实扫码验证；若页面改版需调整 selectors.py。
- 登录成功后 Profile 数据为高敏感凭据；当前目录权限依赖操作系统用户权限，尚未做额外加密。
- 尚无 Scheduler（M4）；SendJob 表空转，dashboard 的今日数据仍为 0。
- 冒烟用的 data/ 已清空，用户首次启动将重建全新数据库。
- Starlette 的 httpx→httpx2 弃用警告仍存在，功能无影响。
- Dry Run 结果仅落盘 artifact JSON，未入库供历史查询（M4 与任务表一并解决）。

## 尝试过但失败的方案
- autogenerate 直接连默认库失败（父目录不存在）→ env.py 迁移前创建 SQLite 父目录解决。
- 基线迁移缺少 sqlmodel 导入 → 显式补 import 解决。
- 内联 Python 检查数据库时 PowerShell 引号转义两次出错 → 改为单引号外层包裹解决。

## 下一步开发顺序
1. 用户真实验证：启动前后端，扫码登录一个账号，绑定好友后 Dry Run，反馈「已定位/未找到/身份存疑」的实际情况。
2. 提交 M2 变更（用户确认后）。
3. M3：纯文字发送 + 发送终态确认（单条 outgoing 消息状态机）+ 失败分类与截图 artifact。
4. M4：Scheduler（随机焰窗、租约、heartbeat、账号互斥）+ 每账号 Worker 进程 + SendJob 入库与今日焰线点亮。
5. M5：原生表情/图片、通知、selector 热更与火花天数校准。
