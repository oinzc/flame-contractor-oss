# Flame Contractor 开机自启（Windows）

## 用途
本项目的调度器是**进程内**的：uvicorn 不在跑 = 自动续火不在跑。开机自启消除
「重启电脑后忘记启动程序 → 断签」这一单点。

## 安装（当前机器执行一次）
```bat
scripts\autostart\install_autostart.bat
```
脚本会把带实际仓库路径的 `flame-contractor-autostart.bat` 写入当前用户的
「启动」文件夹（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`），
免管理员权限。

## 行为
登录 Windows 后自动最小化启动：
- 后端：`backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
- 前端：`frontend` 下 `npm run dev`（控制台 http://localhost:5173）

## 卸载
删除启动文件夹里的 `flame-contractor-autostart.bat` 即可。

## 注意
- 仓库换路径后需重新运行安装脚本（重新生成带新路径的启动脚本）；
- 调度器 `paused` 状态为内存态，重启后默认恢复为「运行」——如需暂停请在控制台操作；
- 仅监听 127.0.0.1，不对外网暴露。
