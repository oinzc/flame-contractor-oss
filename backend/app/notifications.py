"""通知服务：把「必须人工接管」的信号主动推送给用户。

渠道当前仅支持 Bark（官方 API V2：POST JSON 到设备地址）。
- 推送地址来自 `FLAME_BARK_URL`，形如 `https://api.day.app/<device_key>`；
  为空或未配置时本服务为 no-op，不影响调度。
- Bark 官方 JSON 字段：device_key / title / body / badge / sound / group / url / icon。
  `level` 以 query 参数追加（用于 critical / timeSensitive 等提醒级别）。
- 所有 POST 用 httpx，默认短超时，失败只记日志、不抛出（通知失败不影响主流程）。

真实网络发送无法在单元测试中验证，因此提供 `sender` 注入点：测试注入 fake 回调。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 6.0
VALID_LEVELS = {"active", "timeSensitive", "critical", "passive"}
DEFAULT_LEVEL = "active"

Sender = Callable[[str, str], Awaitable[None]]


@dataclass
class BarkNotifier:
    """Bark 推送封装。

    Bark 的 device_key 属于本机私密信息，不入库、不上传，只存于环境变量 / .env。
    """

    base_url: str | None = None
    sender: Sender | None = None
    timeout: float = DEFAULT_TIMEOUT
    level: str = DEFAULT_LEVEL

    # 供测试/调用方观察最近一次通知内容
    last_sent: dict | None = field(default=None, init=False)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.base_url.strip())

    @staticmethod
    def _device_key(base_url: str) -> str:
        """从 base_url 中提取 device_key：取路径中最后一个非空段。
        兼容 `https://api.day.app/<key>` 与 `https://api.day.app/<key>/push`。
        """
        path = urlsplit(base_url).path
        segments = [seg for seg in path.split("/") if seg]
        for segment in reversed(segments):
            if segment != "push":
                return segment
        return ""

    def _resolve_url(self, level: str) -> str | None:
        level = level or self.level
        if level not in VALID_LEVELS:
            level = self.level
        base = (self.base_url or "").strip()
        if not base:
            return None
        return f"{base}?level={level}"

    async def send(self, title: str, content: str, level: str | None = None) -> bool:
        """推送一条通知；未配置或失败时静默 no-op。返回是否成功。"""
        url = self._resolve_url(level)
        if not url:
            return False
        effective_level = (level or self.level) if (level or self.level) in VALID_LEVELS else self.level
        self.last_sent = {"title": title, "content": content, "level": effective_level}

        if self.sender is not None:
            await self.sender(title, content)
            return True

        payload = {
            "device_key": self._device_key(self.base_url or ""),
            "title": title,
            "body": content,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 - 通知失败不影响主流程
            logger.warning("Bark 通知发送失败：%s", type(exc).__name__)
            return False


class Notifier(Protocol):
    """统一的通知接口：Scheduler 只依赖该协议，便于测试用 Null/fake 替换。"""

    @property
    def enabled(self) -> bool: ...

    async def send(self, title: str, content: str, level: str | None = None) -> bool: ...


class NullNotifier:
    """未配置通知时的占位实现：始终 no-op，保持接口一致。"""

    @property
    def enabled(self) -> bool:
        return False

    async def send(self, title: str, content: str, level: str | None = None) -> bool:
        return False
