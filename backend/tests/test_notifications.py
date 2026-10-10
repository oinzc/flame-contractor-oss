"""M5：Bark 通知器——URL/level 解析、device_key 提取、未配置 no-op、sender 注入。"""

import asyncio
import logging

from app.notifications import BarkNotifier, NullNotifier


def test_notifier_disabled_without_url():
    notifier = BarkNotifier(base_url=None)
    assert notifier.enabled is False
    assert asyncio.run(notifier.send("t", "c")) is False


def test_notifier_extracts_device_key():
    assert BarkNotifier._device_key("https://api.day.app/abc123") == "abc123"
    assert BarkNotifier._device_key("https://api.day.app/abc123/push") == "abc123"


def test_resolve_url_appends_level():
    notifier = BarkNotifier(base_url="https://api.day.app/abc123", level="active")
    assert notifier._resolve_url("critical") == "https://api.day.app/abc123?level=critical"
    # 默认 level
    assert notifier._resolve_url(None) == "https://api.day.app/abc123?level=active"


def test_invalid_level_falls_back_to_default():
    notifier = BarkNotifier(base_url="https://api.day.app/abc123", level="active")
    assert notifier._resolve_url("bogus") == "https://api.day.app/abc123?level=active"


def test_invalid_level_logs_warning(caplog):
    """回归：`level="errors"` 这类笔误会静默降级成最低优先级推送（消息照发但不响铃），
    必须留下告警，否则「账号发送出错」这类关键通知会悄悄失去提醒效果。"""
    notifier = BarkNotifier(base_url="https://api.day.app/abc123", level="active")
    with caplog.at_level(logging.WARNING):
        assert notifier._resolve_url("errors") == "https://api.day.app/abc123?level=active"
    assert "errors" in caplog.text


def test_send_uses_injected_sender():
    calls: list[tuple[str, str]] = []

    async def sender(title: str, content: str) -> None:
        calls.append((title, content))

    notifier = BarkNotifier(base_url="https://api.day.app/abc123", sender=sender)
    assert notifier.enabled is True
    result = asyncio.run(notifier.send("标题", "内容", "critical"))
    assert result is True
    assert calls == [("标题", "内容")]
    assert notifier.last_sent == {"title": "标题", "content": "内容", "level": "critical"}


def test_null_notifier_is_noop():
    notifier = NullNotifier()
    assert notifier.enabled is False
    assert asyncio.run(notifier.send("t", "c")) is False
