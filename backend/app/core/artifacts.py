"""artifacts 诊断产物的维护工具：过期清理。

诊断产物（失败截图、发送/定位工件 JSON）属于核心功能（见调研报告 §3.5），
但需有保留期，否则长期无人值守运行会无限膨胀。清理只在启动时执行一次，
策略保守：只删文件不动目录；单个文件删除失败不影响其余文件。
"""

from datetime import datetime, timezone
from pathlib import Path

SECONDS_PER_DAY = 86400


def prune_old_artifacts(
    artifacts_dir: Path,
    retention_days: int,
    *,
    now: datetime | None = None,
) -> int:
    """删除 artifacts_dir 下 mtime 早于保留期的文件，返回删除数量。

    retention_days <= 0 表示关闭清理；目录不存在时视为无可清理。
    """
    if retention_days <= 0:
        return 0
    if not artifacts_dir.exists():
        return 0
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - retention_days * SECONDS_PER_DAY
    removed = 0
    for path in artifacts_dir.rglob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
