from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "flame-contractor.db"


class Settings(BaseSettings):
    app_name: str = "Flame Contractor"
    version: str = "0.1.0"
    api_prefix: str = "/api"
    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    profiles_dir: Path = PROJECT_ROOT / "data" / "profiles"
    artifacts_dir: Path = PROJECT_ROOT / "artifacts"
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )

    model_config = SettingsConfigDict(
        env_prefix="FLAME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
