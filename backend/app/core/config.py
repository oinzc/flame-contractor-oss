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
    # 成功汇总推送：自动发送完成后推送「今日续焰完成 N/M」（Bark）
    notify_on_success: bool = True
    # 连续失败熔断阈值：连续 N 次自动发送终态失败后账号转 cooldown，0=不熔断
    failure_streak_limit: int = 3
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
