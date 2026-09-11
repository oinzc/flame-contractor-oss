# Flame Contractor Windows 常驻运行

这套方案用于同一名 Windows 用户锁屏后保持 Flame Contractor 运行，不使用 Docker。

设计参考：

- Microsoft PowerToys Awake：使用 Windows execution-state request 保持系统唤醒，允许显示器关闭。
- WinSW：明确工作目录、独立日志、失败重启；本项目不直接使用 Session 0 服务，以保留当前 Windows 用户的抖音浏览器 profile。
- FastAPI `app.frontend()`：同一 uvicorn 进程同时提供 API 和 Vite 构建产物，减少常驻进程数。

## 安装并启动

先在控制台暂停自动调度，再执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\runtime\manage_runtime.ps1 -Action Install
```

安装会：

1. 执行前端 `npm.cmd run build`；
2. 注册当前用户的 `Flame Contractor Runtime` 计划任务；
3. 登录 Windows 时启动监督器，任务失败时每分钟重试；
4. 持续检查 `/health`，后端退出或持续不健康时重启已确认由它启动的进程；
5. 申请系统保持唤醒，但不阻止显示器关闭。

常驻页面地址：<http://127.0.0.1:8000/>

## 管理命令

```powershell
# 查看计划任务、进程和健康状态
powershell -ExecutionPolicy Bypass -File scripts\runtime\manage_runtime.ps1 -Action Status

# 停止，保留计划任务
powershell -ExecutionPolicy Bypass -File scripts\runtime\manage_runtime.ps1 -Action Stop

# 再次启动
powershell -ExecutionPolicy Bypass -File scripts\runtime\manage_runtime.ps1 -Action Start

# 停止并删除计划任务
powershell -ExecutionPolicy Bypass -File scripts\runtime\manage_runtime.ps1 -Action Uninstall
```

日志位于 `%LOCALAPPDATA%\FlameContractor\logs`。监督器日志超过 5 MB 时会轮转，每次后端重启会创建独立的 stdout/stderr 日志。

## 边界

- 计划任务仅在当前 Windows 用户登录期间运行；`Win+L` 不会结束会话。
- 保持唤醒会增加耗电，电池供电时也不会自动停止。
- Windows 主动休眠、休眠、关机或登出后无法继续执行；下次登录时会重新启动。
- 更新前端后需重新执行 `-Action Install` 生成新的 `dist`。
- 运行数据、日志和 PID 文件都不进入 Git。
