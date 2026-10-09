# Docker 部署

使用同一套 Linux 容器提供 API、前端和 Chromium。只运行一个应用实例和一个 worker；不使用 Windows 原生容器，不需要桌面或 VNC。

Playwright 固定版本并在镜像内安装匹配的 Chromium；`backend/constraints-docker.txt` 固定已验证的运行依赖。升级依赖时需重新运行容器内回归，不能仅以本机虚拟环境的测试结果代替。

## 平台范围

| 宿主平台 | 方式 | 验证范围 |
| --- | --- | --- |
| Windows x64 | Docker Desktop，Linux containers / WSL2 | 本项目本机验证 |
| Linux x64 | Docker Engine + Compose v2 | Ubuntu 24.04 服务器验证 |
| macOS Intel | Docker Desktop，使用相同配置 | 无实机，不宣称已验收 |
| Linux ARM64 / Apple Silicon | 基础镜像和构建不固定 amd64，可尝试原生 ARM64 构建 | 无实机，浏览器运行仍待验证 |
| Windows 原生容器 / 32 位系统 | 不提供 | 本方案使用 Linux Chromium |

Docker Desktop 必须保持运行。宿主机休眠、关机或退出 Docker 后，容器无法继续调度；服务器推荐 Linux 常驻。

## 首次启动

安装 Docker 和 Compose 后，在仓库根目录执行（Windows 用 PowerShell）：

```text
cp .env.example .env
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(48))"
```

将生成值填入 `.env` 的 `FLAME_SECRET_KEY`，不能保留 `CHANGE_ME`。可选填 `FLAME_BARK_URL`。本文件仅在运行时只读挂载，不进入镜像或 Git。Linux 上将 `.env` 属主设为容器用户 `10001:10001`，权限设为 `600`；Windows 上限制文件为本人可访问。

```text
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 app
```

浏览器打开 <http://127.0.0.1:8000/>。端口冲突时，Windows 使用 `$env:FLAME_HOST_PORT='18001'`，Linux/macOS 使用 `export FLAME_HOST_PORT=18001` 后启动。容器内部监听隔离网络的 `0.0.0.0:8000`，Compose 只发布到宿主 `127.0.0.1`，不能改成公网监听。

全新数据库需在页面创建首位管理员。迁移现有数据库时沿用既有平台账号密码。**抖音使用「截图扫码」登录**；有头「扫码登录」需要桌面，不适用于容器。Windows Chromium Profile 不直接复制到 Linux 容器，重新扫码后新登录态保存在数据卷中。

## 持久化与维护

`data` 卷保存 SQLite 和各账号 Profile；`artifacts` 卷保存诊断产物。SQLite 使用本地 Docker 卷，不放在网络共享盘。`docker compose down` 保留数据，**不要使用 `down -v`**。`.env` 也应独立安全备份。

容器以 UID 10001 的非 root 用户运行，启用 init 回收浏览器子进程，提供 512 MB 共享内存、日志轮转、健康检查和退出后自动重启。健康检查失败仅标记 unhealthy；不会自动杀掉可能正在发送的进程。浏览器仍消耗实际宿主内存，Docker 不会解决内存不足。

更新前暂停调度并确认无运行任务；备份数据库后再构建和重建容器。不要 scale 多实例，不要同时启用原生服务与容器的调度。数据库迁移随应用启动执行；回滚镜像前确认迁移兼容性，必要时从备份恢复。

数据库在线一致性备份：

```text
docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/app/data/flame-contractor.db'); d=sqlite3.connect('/tmp/flame-backup.db'); s.backup(d); d.close(); s.close()"
docker compose cp app:/tmp/flame-backup.db ./flame-backup.db
```

数据库备份不包含 Profile；备份登录态前先停止容器，再备份 data 卷。将已有库迁入数据卷前，先在旧控制台暂停调度，确认无运行任务，再用 SQLite backup API 生成副本；不要直接复制运行中的 DB/WAL 文件。导入副本必须保持 `scheduler.paused=1`，设置正确的 UID/GID，重新扫码、定位校验后才恢复服务器调度。

图片模板必须使用容器内可读路径，例如 `/app/data/images/example.png`。宿主 Windows 的 `C:\...` 或 Linux 的外部路径不会自动进入容器；将图片放入 data 卷并更新模板路径后使用。本轮迁移没有图片模板，不需要转换图片路径。

## 服务器访问

当前部署目录为 `/opt/flame-contractor-docker`。旧的 `/opt/flame-contractor` 原生部署保留作备份，systemd 应用服务停用，不能与容器同时运行。

在当前 Windows 电脑双击 `scripts/runtime/open_server_console.cmd`，或执行：

```powershell
ssh -i "$env:USERPROFILE\.ssh\codex_alicloud_ed25519" -N -L 127.0.0.1:18000:127.0.0.1:8000 root@47.253.197.95
```

访问 <http://127.0.0.1:18000/>。隧道关闭只影响网页访问，不停止服务器调度。其他电脑需要自己的 SSH 密钥授权，再运行相同隧道命令；不需要域名。

服务器管理：SSH 登录后 `cd /opt/flame-contractor-docker`，使用 `docker compose ps`、`logs --tail 100 app`、`stop`、`up -d`。配置变化用 `up -d --force-recreate`；修改挂载的密钥会使旧登录会话失效。

2026-10-09 验证：Windows 构建、启动、非 root Chromium、默认密钥拒绝、重建后数据保留通过；Linux 最终镜像内 pytest 为 210 passed、2 warnings。两个警告为 TestClient/httpx 弃用与模板删除行数不匹配，尚未处理。未实测真实抖音扫描、发送及 macOS/ARM64。

浏览器版本匹配、进程回收和容器浏览器配置参考 [Playwright 官方 Docker 文档](https://playwright.dev/python/docs/docker)。
