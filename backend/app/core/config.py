from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "flame-contractor.db"
AUTO_SCAN_INTERVAL_MIN_HOURS = 1
AUTO_SCAN_INTERVAL_MAX_HOURS = 168


class Settings(BaseSettings):
    app_name: str = "Flame Contractor"
    version: str = "0.1.0"
    api_prefix: str = "/api"
    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    profiles_dir: Path = PROJECT_ROOT / "data" / "profiles"
    artifacts_dir: Path = PROJECT_ROOT / "artifacts"
    # 常驻模式可由同一个 FastAPI 进程托管前端构建产物；开发模式继续使用 Vite。
    serve_frontend: bool = False
    frontend_dist_dir: Path = PROJECT_ROOT / "frontend" / "dist"
    # Bark 通知推送地址（可选）：形如 https://api.day.app/<device_key>，空则不推送
    bark_url: str | None = None
    # artifacts 诊断产物保留天数（失败截图/工件 JSON），0 表示永不过期
    artifact_retention_days: int = 30
    # 窗口过期策略：skip=窗口已结束不再派发（防深夜打扰，默认）；dispatch=到点即发
    window_expiry_policy: str = "skip"
    # 固定发送时刻的宽限期（分钟）。固定时刻本身被当作「窗口终点」，若不设宽限，
    # 计划在到点那一刻就被判「窗口已结束」而永远发不出去；宽限期内仍按到点处理，
    # 超过宽限才转 SKIPPED（保持「错过不深夜补发」的原意）。
    fixed_time_grace_minutes: int = 30
    # 自动调度到点是否始终发一遍：默认 False 保持「每天每个好友只发一次」；
    # 开启后定时任务忽略当天已发送记录（含手动强制发送）再发一遍。
    # 注意：「结果未确认」仍然绝不自动重发，这条边界不受该开关影响。
    scheduled_always_send: bool = False
    # 成功汇总推送：自动发送完成后推送「今日续焰完成 N/M」（Bark）
    notify_on_success: bool = True
    # 连续失败熔断阈值：连续 N 次自动发送终态失败后账号转 cooldown，0=不熔断
    failure_streak_limit: int = 3
    # 好友间发送冷却下限（秒）：批量发送时两个好友之间至少等待的时长
    send_cooldown_seconds: float = 3.0
    # 好友间发送冷却随机抖动上限（秒）：实际等待 = 下限 + uniform(0, 抖动)，默认
    # 3~8 秒随机，消除「固定 3 秒」的机器节律特征；0 表示回到固定间隔。
    send_cooldown_jitter_seconds: float = 5.0
    # 自动调度同时发送的账号数上限：多账号同时到点时未派发的计划保持 pending，
    # 由下一轮 tick（60 秒后）重试，形成天然错峰；同时避免同一出口 IP / 设备
    # 在同一时刻并行多个浏览器会话。1 = 全局串行（好友级准点派发不受此限，
    # 准点语义优先）。
    max_concurrent_sends: int = 1
    # 登录判定容忍次数：连续 N 次判定失败才把账号标记为登录失效。单次判定抖动
    # （页面加载慢、临时验证页、网络波动）不该锁死账号——调度器会跳过非 HEALTHY
    # 账号的扫描与发送，一次误判就会让账号停摆且只能人工重新扫码恢复。
    auth_check_tolerance: int = 2
    # 登录判定预算（秒）：页面打开后等会话列表出现的时长。判定成功立即返回，放宽只
    # 影响「确实没登录」时的确认耗时。默认 20 秒（原 8 秒偏紧，冷启动已接近上限）。
    login_check_timeout: float = 20.0
    # 单次会话扫描的总预算（秒）。抖音页面在滚动读会话时可能让某个 Playwright 调用
    # 永远不返回；扫描任务一旦挂住，该账号的「扫描中」占用就永不释放，随后所有发送
    # 都会被 409 拒绝（2026-11-01 实测：卡住 2 小时，准点发送每分钟失败并推送一次）。
    # 超时后中止本次扫描并释放占用，宁可缺一次官方快照，也不能锁死续火。
    scan_timeout_seconds: float = 600.0
    # 会话官方火花状态自动同步间隔（小时）；官方页面只提供天级状态，默认每日同步一次
    auto_scan_interval_hours: int = Field(
        default=24,
        ge=AUTO_SCAN_INTERVAL_MIN_HOURS,
        le=AUTO_SCAN_INTERVAL_MAX_HOURS,
    )
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    # 认证：签名 token 的密钥（上线务必用 FLAME_SECRET_KEY 覆盖默认值）
    secret_key: str = "flame-contractor-dev-secret-change-me"
    # 仅供旧库升级时认领无属主数据；全新安装由页面初始化首位管理员
    bootstrap_username: str = "admin"
    bootstrap_password: str = "admin"

    model_config = SettingsConfigDict(
        env_prefix="FLAME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
