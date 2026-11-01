"""异常转述：把内部异常收敛成「用户看得懂的一句原因」。

后台链路（调度器、浏览器执行层）里的失败最终会以两种形式露给用户：手机推送与
任务记录。以前这里一律写 `type(exc).__name__`，于是最常见的一类失败变成了
「（HTTPException）」——它既没说明账号到底卡在哪，也没给出任何可操作方向
（2026-11-01 实测：同一个账号连续推送五条「HTTPException」，实际原因是这台机器
上一次会话扫描挂住、账号一直被判定为「正在扫描会话」）。

HttpException 的 detail 是链路自己写的中文原因，优先取它；其余异常退回
str(exc)，最后才用类名兜底。
"""

from __future__ import annotations

import asyncio


def describe_exception(exc: BaseException, fallback: str | None = None) -> str:
    """返回一句可直接展示的失败原因，绝不返回空串。"""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    text = str(exc).strip()
    if text:
        return text
    return fallback or type(exc).__name__


def discard_task_result(task: asyncio.Task) -> None:
    """吞掉被放弃任务的结果，避免「Task exception was never retrieved」噪音。

    超时收束的任务会被 cancel 后立即放手（不等它结束），它的异常既不属于任何
    调用方，也不该污染日志。
    """
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:
        pass
