"""M3：消息渲染、发送预览与发送终态确认的测试。"""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.execution.message_renderer import render_message
from app.execution.selectors import CHAT_EDITOR
from app.models import JobStatus, SendAttempt, SendJob
from tests.fakes import FakePage, create_account, make_app


# ---------------------------------------------------------------- renderer


def test_render_message_replaces_allowed_variables():
    rendered = render_message(
        "{{target_name}}，第 {{streak_days}} 天，{{weekday}}，{{date}}。",
        target_name="好友A",
        streak_days=7,
        timezone="Asia/Shanghai",
    )
    assert rendered.startswith("好友A，第 7 天，星期")
    assert "{{" not in rendered


def test_render_message_keeps_unknown_tokens():
    rendered = render_message("你好 {{target_name}} {{unknown_var}}", target_name="A", streak_days=0)
    assert rendered == "你好 A {{unknown_var}}"


# ---------------------------------------------------------------- preview


def _account_with_targets(client: TestClient, template: bool = True) -> dict:
    account = create_account(client)
    if template:
        template_created = client.post(
            "/api/templates",
            json={"name": "T", "content": "{{target_name}}，第 {{streak_days}} 天。"},
        ).json()
        client.post(
            "/api/targets",
            json={
                "accountId": account["id"],
                "nicknameSnapshot": "好友A",
                "templateId": template_created["id"],
                "streakDays": 5,
            },
        )
    else:
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
        )
    return account


def test_send_preview_renders_message(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        payload = client.get(f"/api/accounts/{account['id']}/send/preview").json()
        assert payload["businessDate"]
        item = payload["items"][0]
        assert item["targetName"] == "好友A"
        assert item["message"] == "好友A，第 5 天。"


def test_send_preview_reports_missing_template(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        account = _account_with_targets(client, template=False)
        payload = client.get(f"/api/accounts/{account['id']}/send/preview").json()
        assert payload["items"][0]["message"] is None
        assert payload["items"][0]["reason"] == "未绑定可用模板"


# ---------------------------------------------------------------- send flow


def _page_for_send(**kwargs) -> FakePage:
    return FakePage(
        logged_in=True,
        conversation_titles=("好友A",),
        right_title="好友A",
        editor_present=True,
        **kwargs,
    )


def test_send_succeeds_and_updates_everything(settings):
    app = make_app(settings, [_page_for_send()])
    with TestClient(app) as client:
        account = _account_with_targets(client)

        result = client.post(f"/api/accounts/{account['id']}/send")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["overall"] == "ok"
        assert payload["results"][0]["status"] == "succeeded"
        assert payload["artifactPath"]

        progress = client.get(f"/api/accounts/{account['id']}/send/progress")
        assert progress.status_code == 200, progress.text
        assert progress.json()["status"] == "completed"
        assert progress.json()["completed"] == progress.json()["total"] == 1
        assert progress.json()["stage"] == "发送完成"

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status == JobStatus.SUCCEEDED
            assert job.confirmed_at is not None
            attempts = session.exec(select(SendAttempt)).all()
            assert len(attempts) == 1
            assert attempts[0].status == "succeeded"

        targets = client.get("/api/targets").json()
        assert targets[0]["streakDays"] == 6
        assert targets[0]["lastConfirmedAt"] is not None

        updated = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account["id"]
        )
        assert updated["lastSuccessAt"] is not None


def test_send_accepts_spark_badge_suffix_in_right_title(settings):
    """火花徽标追加到右侧标题时，仍按主昵称严格确认并允许发送。"""
    from app.execution.douyin_chat import DouyinChat

    for right_title in ("好友A\n16", "好友A\n重燃中 2/3", "好友A\n4 天后消失"):
        assert DouyinChat._primary_panel_title(right_title) == "好友A"
    assert DouyinChat._primary_panel_title("好友A同学\n16") == "好友A同学"

    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友A",),
                right_title="好友A\n4 天后消失",
                editor_present=True,
            )
        ],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        result = client.post(f"/api/accounts/{account['id']}/send")
        assert result.status_code == 200, result.text
        assert result.json()["results"][0]["status"] == "succeeded", result.json()


def test_send_waits_for_editor_to_render_after_open(settings):
    """会话切换后编辑器异步出现时，发送应等待而不是误报 DOM 改版。"""

    class DelayedEditorPage(FakePage):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._editor_misses = 3

        async def query_selector(self, selector):
            if selector == CHAT_EDITOR and self._editor_misses:
                self._editor_misses -= 1
                return None
            return await super().query_selector(selector)

    app = make_app(
        settings,
        [
            DelayedEditorPage(
                logged_in=True,
                conversation_titles=("好友A",),
                right_title="好友A",
                editor_present=True,
            )
        ],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        result = client.post(f"/api/accounts/{account['id']}/send")
        assert result.status_code == 200, result.text
        assert result.json()["results"][0]["status"] == "succeeded", result.json()


def test_send_second_run_same_day_is_duplicate(settings):
    app = make_app(settings, [_page_for_send(), _page_for_send()])
    with TestClient(app) as client:
        account = _account_with_targets(client)

        first = client.post(f"/api/accounts/{account['id']}/send").json()
        assert first["results"][0]["status"] == "succeeded"

        second = client.post(f"/api/accounts/{account['id']}/send").json()
        assert second["results"][0]["status"] == "duplicate_succeeded"

        with Session(app.state.engine) as session:
            jobs = session.exec(select(SendJob)).all()
            assert len(jobs) == 1
        assert client.get("/api/targets").json()[0]["streakDays"] == 6  # 只加过一次


def test_force_send_ignores_same_day_success(settings):
    """手动强制发送绕过当天成功槽位，并创建独立可审计任务。"""
    app = make_app(settings, [_page_for_send(), _page_for_send()])
    with TestClient(app) as client:
        account = _account_with_targets(client)

        first = client.post(f"/api/accounts/{account['id']}/send").json()
        assert first["results"][0]["status"] == "succeeded"

        forced = client.post(f"/api/accounts/{account['id']}/send?force=true")
        assert forced.status_code == 200, forced.text
        assert forced.json()["results"][0]["status"] == "succeeded", forced.json()

        with Session(app.state.engine) as session:
            jobs = session.exec(select(SendJob)).all()
            assert len(jobs) == 2
            assert all(job.status == JobStatus.SUCCEEDED for job in jobs)
            assert len({job.message_slot_id for job in jobs}) == 2
            assert any(":force:" in job.message_slot_id for job in jobs)
        assert client.get("/api/targets").json()[0]["streakDays"] == 7


def test_regular_send_after_force_success_is_duplicate(settings):
    """强制发送先完成时，后续普通/自动发送仍保持当天防重复。"""
    app = make_app(settings, [_page_for_send(), _page_for_send()])
    with TestClient(app) as client:
        account = _account_with_targets(client)

        forced = client.post(f"/api/accounts/{account['id']}/send?force=true")
        assert forced.status_code == 200, forced.text
        assert forced.json()["results"][0]["status"] == "succeeded", forced.json()

        regular = client.post(f"/api/accounts/{account['id']}/send")
        assert regular.status_code == 200, regular.text
        assert regular.json()["results"][0]["status"] == "duplicate_succeeded"

        with Session(app.state.engine) as session:
            jobs = session.exec(select(SendJob)).all()
            assert len(jobs) == 1
            assert ":force:" in jobs[0].message_slot_id
        assert client.get("/api/targets").json()[0]["streakDays"] == 6


def test_send_unconfirmed_outcome_blocks_retry(settings):
    app = make_app(
        settings,
        [_page_for_send(clear_on_enter=False), _page_for_send(clear_on_enter=False)],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        first = client.post(f"/api/accounts/{account['id']}/send").json()
        assert first["overall"] == "partial"
        assert first["results"][0]["status"] == "unknown_requires_review"

        second = client.post(f"/api/accounts/{account['id']}/send").json()
        assert second["results"][0]["status"] == "duplicate_unknown"

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status == JobStatus.UNKNOWN_REQUIRES_REVIEW


def test_scheduled_slot_ignores_same_day_manual_force(settings):
    """调度器传入的「按业务日固定」槽位：当天手动强制发过，定时到点仍再发一遍。

    scheduled_always_send 的实现基础：槽位后缀固定为 sched<业务日>，与手动强制的
    随机 :force: 槽位互不冲突，因此同一天两条都成立；重试复用同一槽位不会多发。
    """
    import asyncio

    app = make_app(settings, [_page_for_send(), _page_for_send()])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        manager = app.state.browser_manager

        forced = asyncio.run(manager.run_send(account["id"], force=True))
        assert forced["overall"] == "ok", forced
        with Session(app.state.engine) as session:
            jobs = session.exec(select(SendJob)).all()
        assert len(jobs) == 1
        assert ":force:" in jobs[0].message_slot_id

        resend = asyncio.run(manager.run_send(account["id"], force_run_id="sched20260827"))
        assert resend["results"][0]["status"] == "succeeded", resend

        with Session(app.state.engine) as session:
            slots = {job.message_slot_id for job in session.exec(select(SendJob)).all()}
        assert len(slots) == 2
        assert any(slot.endswith(":force:sched20260827") for slot in slots)


def test_force_send_still_blocks_unconfirmed_outcome(settings):
    """强制模式也不能绕过当天未人工核对的未知结果。"""
    app = make_app(
        settings,
        [_page_for_send(clear_on_enter=False), _page_for_send()],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        first = client.post(f"/api/accounts/{account['id']}/send").json()
        assert first["results"][0]["status"] == "unknown_requires_review"

        forced = client.post(f"/api/accounts/{account['id']}/send?force=true")
        assert forced.status_code == 200, forced.text
        assert forced.json()["results"][0]["status"] == "duplicate_unknown"

        with Session(app.state.engine) as session:
            jobs = session.exec(select(SendJob)).all()
            assert len(jobs) == 1
            assert jobs[0].status == JobStatus.UNKNOWN_REQUIRES_REVIEW


def test_send_failure_marker_then_retry_succeeds(settings):
    app = make_app(
        settings,
        [_page_for_send(failure_marker=True), _page_for_send()],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        first = client.post(f"/api/accounts/{account['id']}/send").json()
        assert first["results"][0]["status"] == "failed_retryable"

        second = client.post(f"/api/accounts/{account['id']}/send").json()
        assert second["results"][0]["status"] == "succeeded"

        with Session(app.state.engine) as session:
            jobs = session.exec(select(SendJob)).all()
            assert len(jobs) == 1
            assert jobs[0].status == JobStatus.SUCCEEDED
            assert jobs[0].attempt_count == 2


def test_send_ambiguous_target_fails_safe(settings):
    app = make_app(
        settings,
        [FakePage(logged_in=True, conversation_titles=("好友A",), right_title="好友A同学")],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["results"][0]["status"] == "ambiguous"
        assert result["results"][0]["failureClass"] == "target_uncertain"

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status == JobStatus.FAILED_TERMINAL
        # 未发送成功，火花天数不变
        assert client.get("/api/targets").json()[0]["streakDays"] == 5


def test_send_not_logged_in_marks_auth_expired(settings):
    """发送遇登录失效：overall=auth_expired；连续两次判定失败才标记账号失效。

    单次判定抖动不改状态、不锁死调度（否则要人工重新扫码才能恢复），第一次失败
    只计数并记为「未确认」。
    """
    app = make_app(settings, [FakePage(logged_in=False), FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        first = client.post(f"/api/accounts/{account['id']}/send").json()
        assert first["overall"] == "auth_expired"
        assert "未确认" in first["detail"]

        status_after_first = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account["id"]
        )["status"]
        assert status_after_first != "auth_expired"

        client.post(f"/api/accounts/{account['id']}/send")
        updated = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account["id"]
        )
        assert updated["status"] == "auth_expired"


def test_send_works_with_real_playwright_page_shape(settings):
    """回归：发送链路不得依赖假页面独有的 type_text/press_key 协议方法。"""
    from tests.fakes import RealApiPage

    app = make_app(
        settings,
        [
            RealApiPage(
                logged_in=True,
                conversation_titles=("好友A",),
                right_title="好友A",
            )
        ],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        result = client.post(f"/api/accounts/{account['id']}/send")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["results"][0]["status"] == "succeeded", payload


def test_editor_with_zero_width_chars_counts_as_cleared(settings):
    """抖音输入框发送后残留零宽字符：必须视为「已清空」。"""
    import asyncio

    from app.execution.douyin_chat import DouyinChat
    from tests.fakes import FakePage

    page = FakePage(logged_in=True, editor_present=True)
    page.editor_text = "\u200b"  # 零宽空格
    chat = DouyinChat(page)
    assert asyncio.run(chat.editor_is_cleared()) is True


def test_bubble_evidence_upgrades_unknown_to_succeeded(settings):
    """真实页面路径：消息已进入气泡列表但状态指示未消失 → 按成功核销。"""
    from tests.fakes import FakePage

    class BubblePage(FakePage):
        async def wait_for_function(self, _script, arg=None, timeout=None):
            return True  # 模拟消息列表已出现目标文本

    app = make_app(
        settings,
        [
            BubblePage(
                logged_in=True,
                conversation_titles=("好友A",),
                right_title="好友A",
                clear_on_enter=False,  # 模拟状态指示不消失（零宽字符类异常）
            )
        ],
    )
    with TestClient(app) as client:
        account = _account_with_targets(client)

        result = client.post(f"/api/accounts/{account['id']}/send")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["results"][0]["status"] == "succeeded", payload
        assert "气泡" in (payload["results"][0]["detail"] or "")

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status == JobStatus.SUCCEEDED


def test_send_skips_target_without_template(settings):
    app = make_app(settings, [_page_for_send()])
    with TestClient(app) as client:
        account = _account_with_targets(client, template=False)
        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["overall"] == "ok"
        assert result["results"][0]["status"] == "skipped"


def test_send_uses_account_business_date(settings, engine):
    from app.models import Account as AccountModel

    app = make_app(settings, [_page_for_send()])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        client.post(f"/api/accounts/{account['id']}/send")

    with Session(engine) as session:
        job = session.exec(select(SendJob)).one()
        assert job.business_date == date.today()
        account = session.get(AccountModel, job.account_id)
        assert account is not None


def test_create_image_template_validates(settings):
    """图片模板需 imagePath；文本模板需内容。"""
    app = make_app(settings, [])
    with TestClient(app) as client:
        missing = client.post(
            "/api/templates", json={"name": "T", "type": "image"}
        )
        assert missing.status_code == 422

        ok = client.post(
            "/api/templates",
            json={"name": "T", "type": "image", "imagePath": "D:/pic.png"},
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["type"] == "image"
        assert ok.json()["imagePath"] == "D:/pic.png"


def test_send_image_uploads_and_succeeds(settings):
    """图片模板：图片注入到 input[type=file] 并成功发送（图片气泡真实出现）。"""
    app = make_app(settings, [_page_for_send(image_bubble_render=True)])
    with TestClient(app) as client:
        account = create_account(client)
        tmpl = client.post(
            "/api/templates",
            json={"name": "Img", "type": "image", "imagePath": "D:/test.png"},
        ).json()
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": tmpl["id"]},
        )

        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["results"][0]["status"] == "succeeded", result

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status == JobStatus.SUCCEEDED

        targets = client.get("/api/targets").json()
        assert targets[0]["streakDays"] == 1  # 成功后火花天数 +1


def test_send_image_not_misreads_avatar_as_success(settings):
    """回归：图片气泡未真实出现（只有头像）时，绝不能误判成功。"""
    app = make_app(settings, [_page_for_send(image_bubble_render=False)])
    with TestClient(app) as client:
        account = create_account(client)
        tmpl = client.post(
            "/api/templates",
            json={"name": "Img", "type": "image", "imagePath": "D:/test.png"},
        ).json()
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": tmpl["id"]},
        )

        result = client.post(f"/api/accounts/{account['id']}/send").json()
        status = result["results"][0]["status"]
        # 不误判成功；可能 unknown_requires_review（待人工复核）
        assert status != "succeeded", result

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status != JobStatus.SUCCEEDED


def test_send_image_injects_path_into_file_input():
    """图片发送：点附件图标 → filechooser 注入路径 → 确认弹窗发送 → 图片气泡出现。"""
    import asyncio

    from app.execution.douyin_chat import DouyinChat

    page = FakePage(logged_in=True, editor_present=True, image_bubble_render=True)
    chat = DouyinChat(page, sleep=lambda _s: asyncio.sleep(0))
    asyncio.run(chat.send_image("D:/some/image.png"))
    # 附件图标被点击、图片路径被注入 filechooser、确认弹窗发送被点击
    assert page.attachment_clicked is True
    assert page.image_input_log == ["D:/some/image.png"]
    assert chat.last_bubble_seen is True


# ---------------------------------------------------------------- 单好友强制发送


def _target_with_template(client: TestClient, account: dict, template: dict) -> str:
    resp = client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "好友A",
            "templateId": template["id"],
            "streakDays": 5,
        },
    )
    return resp.json()["id"]


def test_send_target_with_custom_text(settings):
    """单好友手动发送：直接输入文本，绕过当天成功记录，成功并落库。"""
    app = make_app(settings, [_page_for_send(), _page_for_send()])
    with TestClient(app) as client:
        account = create_account(client)
        target_id = client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A", "streakDays": 3},
        ).json()["id"]

        result = client.post(
            f"/api/accounts/{account['id']}/targets/{target_id}/send",
            json={"content": "你好 {{target_name}}，第 {{streak_days}} 天。"},
        )
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["overall"] == "ok"
        assert payload["results"][0]["status"] == "succeeded"
        assert payload["results"][0]["message"] == "你好 好友A，第 3 天。"

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status == JobStatus.SUCCEEDED
            assert job.template_id is None
            assert ":force:" in job.message_slot_id
        assert client.get("/api/targets").json()[0]["streakDays"] == 4


def test_send_target_with_template(settings):
    """单好友手动发送：指定模板，发送成功。"""
    app = make_app(settings, [_page_for_send(), _page_for_send()])
    with TestClient(app) as client:
        account = create_account(client)
        tmpl = client.post(
            "/api/templates", json={"name": "T", "content": "{{target_name}}，第 {{streak_days}} 天。"}
        ).json()
        target_id = _target_with_template(client, account, tmpl)

        result = client.post(
            f"/api/accounts/{account['id']}/targets/{target_id}/send",
            json={"templateId": tmpl["id"]},
        )
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["overall"] == "ok"
        assert payload["results"][0]["status"] == "succeeded"
        assert payload["results"][0]["message"] == "好友A，第 5 天。"

        with Session(app.state.engine) as session:
            job = session.exec(select(SendJob)).one()
            assert job.status == JobStatus.SUCCEEDED
            assert job.template_id == tmpl["id"]
            assert ":force:" in job.message_slot_id
        assert client.get("/api/targets").json()[0]["streakDays"] == 6


def test_send_target_requires_template_or_content(settings):
    """模板与内容都不提供 -> 422；同时提供 -> 422。"""
    app = make_app(settings, [])
    with TestClient(app) as client:
        account = create_account(client)
        target_id = client.post(
            "/api/targets", json={"accountId": account["id"], "nicknameSnapshot": "好友A"}
        ).json()["id"]

        neither = client.post(
            f"/api/accounts/{account['id']}/targets/{target_id}/send",
            json={},
        )
        assert neither.status_code == 422

        both = client.post(
            f"/api/accounts/{account['id']}/targets/{target_id}/send",
            json={"templateId": "x", "content": "y"},
        )
        assert both.status_code == 422


def test_send_target_again_same_day_creates_new_slot(settings):
    """同一天再次强制发送单好友：生成独立 force 槽位，仍真实发送。"""
    app = make_app(settings, [_page_for_send(), _page_for_send()])
    with TestClient(app) as client:
        account = create_account(client)
        target_id = client.post(
            "/api/targets", json={"accountId": account["id"], "nicknameSnapshot": "好友A"}
        ).json()["id"]

        first = client.post(
            f"/api/accounts/{account['id']}/targets/{target_id}/send", json={"content": "一次"}
        ).json()
        assert first["results"][0]["status"] == "succeeded"

        second = client.post(
            f"/api/accounts/{account['id']}/targets/{target_id}/send", json={"content": "二次"}
        ).json()
        assert second["results"][0]["status"] == "succeeded"

        with Session(app.state.engine) as session:
            jobs = session.exec(select(SendJob)).all()
            assert len(jobs) == 2
            assert len({job.message_slot_id for job in jobs}) == 2
            assert all(":force:" in job.message_slot_id for job in jobs)
        assert client.get("/api/targets").json()[0]["streakDays"] == 2


def test_send_target_blocks_unknown_outcome(settings):
    """强制模式也不能绕过当天未人工核对的未知结果。"""
    app = make_app(
        settings,
        [_page_for_send(clear_on_enter=False), _page_for_send(clear_on_enter=False)],
    )
    with TestClient(app) as client:
        account = create_account(client)
        target_id = client.post(
            "/api/targets", json={"accountId": account["id"], "nicknameSnapshot": "好友A"}
        ).json()["id"]

        first = client.post(
            f"/api/accounts/{account['id']}/targets/{target_id}/send", json={"content": "一次"}
        ).json()
        assert first["results"][0]["status"] == "unknown_requires_review"

        second = client.post(
            f"/api/accounts/{account['id']}/targets/{target_id}/send", json={"content": "二次"}
        ).json()
        assert second["results"][0]["status"] == "duplicate_unknown"
