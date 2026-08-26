import re
from datetime import datetime
from zoneinfo import ZoneInfo

ALLOWED_VARIABLES = {"target_name", "streak_days", "weekday", "date"}
_TOKEN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def render_message(
    content: str,
    *,
    target_name: str,
    streak_days: int,
    timezone: str = "Asia/Shanghai",
) -> str:
    """用受控变量渲染模板；未知变量原样保留（创建时已拒绝，此处仅兜底）。"""
    now = datetime.now(ZoneInfo(timezone))
    weekday = f"星期{'一二三四五六日'[now.weekday()]}"
    values = {
        "target_name": target_name,
        "streak_days": str(streak_days),
        "weekday": weekday,
        "date": now.strftime("%Y/%m/%d"),
    }

    def replace(match: re.Match) -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return _TOKEN.sub(replace, content)
