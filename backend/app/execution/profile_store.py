import re
from pathlib import Path

from ..core.config import Settings

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def profile_dir(settings: Settings, account_id: str) -> Path:
    """解析账号专属浏览器 Profile 目录；拒绝非规范 ID 以防路径逃逸。"""
    if not _SAFE_ID.match(account_id):
        raise ValueError("非法的账号 ID")
    return settings.profiles_dir / account_id


def ensure_profile_dir(settings: Settings, account_id: str) -> Path:
    path = profile_dir(settings, account_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
