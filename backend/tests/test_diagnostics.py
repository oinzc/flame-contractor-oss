"""M6-1：失败诊断截图、工件增强（meta/URL/截图路径）与 artifacts 保留期清理。"""

import json
import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from pathlib import Path

from app.core.artifacts import prune_old_artifacts
from app.execution.selectors import bundle_digest
from tests.fakes import FakePage, make_app


def _page_for_send(**kwargs) -> FakePage:
    params = dict(
        logged_in=True,
        conversation_titles=("好友A",),
        right_title="好友A",
        editor_present=True,
    )
    params.update(kwargs)
    return FakePage(**params)


def _account_with_target(client: TestClient) -> dict:
    account = client.post("/api/accounts", json={"displayName": "测试账号"}).json()
    template = client.post(
        "/api/templates",
        json={"name": "T", "content": "{{target_name}}，第 {{streak_days}} 天。"},
    ).json()
    client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "好友A",
            "templateId": template["id"],
            "streakDays": 5,
        },
    )
    return account


def test_send_failure_captures_screenshot_and_meta(settings):
    """失败标记 → failed_retryable：结果与工件都带截图路径、页面 URL 与版本 meta。"""
    app = make_app(settings, [_page_for_send(failure_marker=True)])
    with TestClient(app) as client:
        account = _account_with_target(client)
        payload = client.post(f"/api/accounts/{account['id']}/send").json()

        result = payload["results"][0]
        assert result["status"] == "failed_retryable"
        assert result["screenshot"] and result["screenshot"].endswith(".png")
        assert Path(result["screenshot"]).exists()
        assert result["pageUrl"] == "https://www.douyin.com/chat"

        artifact = json.loads(Path(payload["artifactPath"]).read_text(encoding="utf-8"))
        assert artifact["meta"]["app_version"] == settings.version
        assert artifact["meta"]["bundle_digest"] == bundle_digest()
        assert artifact["meta"]["bundle_digest"]


def test_unknown_outcome_captures_screenshot(settings):
    """无失败标记但气泡未确认 → unknown：同样保留现场截图。"""
    app = make_app(
        settings,
        [_page_for_send(sticker_items=("比心",), image_bubble_render=False)],
    )
    with TestClient(app) as client:
        template = client.post(
            "/api/templates",
            json={"name": "S", "type": "sticker", "stickerName": "比心"},
        ).json()
        account = client.post("/api/accounts", json={"displayName": "测试账号"}).json()
        client.post(
            "/api/targets",
            json={
                "accountId": account["id"],
                "nicknameSnapshot": "好友A",
                "templateId": template["id"],
                "streakDays": 1,
            },
        )
        payload = client.post(f"/api/accounts/{account['id']}/send").json()
        result = payload["results"][0]
        # 无气泡 → 不误判成功；失败标记可见与否决定 failed_retryable/unknown，两者都应留截图
        assert result["status"] in ("unknown_requires_review", "failed_retryable"), result
        assert result["screenshot"] and Path(result["screenshot"]).exists()


def test_not_found_captures_screenshot(settings):
    """列表里没有该好友 → not_found（终态）：截图留证。"""
    app = make_app(settings, [_page_for_send(conversation_titles=())])
    with TestClient(app) as client:
        account = _account_with_target(client)
        payload = client.post(f"/api/accounts/{account['id']}/send").json()
        result = payload["results"][0]
        assert result["status"] == "not_found"
        assert result["screenshot"] and Path(result["screenshot"]).exists()


def test_bundle_digest_is_stable_string(settings):
    assert isinstance(bundle_digest(), str) and len(bundle_digest()) == 12
    assert bundle_digest() == bundle_digest()


def test_prune_old_artifacts(settings, tmp_path):
    """保留期内文件保留，超期文件删除；retention_days=0 关闭清理。"""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    old_time = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old, (old_time, old_time))
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)

    assert prune_old_artifacts(tmp_path, 30, now=now) == 1
    assert not old.exists() and new.exists()

    assert prune_old_artifacts(tmp_path, 0, now=now) == 0
    assert prune_old_artifacts(tmp_path / "missing", 30, now=now) == 0
