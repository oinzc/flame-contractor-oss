import asyncio

from .douyin_chat import DouyinChat


class SendConfirmer:
    """观察单条已触发消息的发送终态。

    原则（参考 unmev/douyin-auto-fire 的终态确认设计）：
    - 失败标记出现 → failed_retryable；
    - 输入框连续多次轮询为空（消息已提交）且无失败标记 → succeeded；
    - 超时仍未确定 → unknown_requires_review（绝不判成功）。
    """

    def __init__(
        self,
        chat: DouyinChat,
        *,
        timeout: float = 15.0,
        poll_interval: float = 0.5,
        stabilize_polls: int = 3,
        sleep=asyncio.sleep,
    ):
        self._chat = chat
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._stabilize_polls = stabilize_polls
        self._sleep = sleep

    async def confirm(self) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        clean_polls = 0
        while True:
            if await self._chat.failure_marker_visible():
                return "failed_retryable"
            cleared = await self._chat.editor_is_cleared()
            if cleared:
                clean_polls += 1
                if clean_polls >= self._stabilize_polls:
                    return "succeeded"
            else:
                clean_polls = 0
            if loop.time() >= deadline:
                return "unknown_requires_review"
            await self._sleep(self._poll_interval)
