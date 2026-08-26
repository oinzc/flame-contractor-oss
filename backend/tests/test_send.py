"""M3：消息渲染、发送预览与发送终态确认的测试。"""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.execution.message_renderer import render_message
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
    app = make_app(settings, [FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = _account_with_targets(client)
        result = client.post(f"/api/accounts/{account['id']}/send").json()
        assert result["overall"] == "auth_expired"

        updated = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account["id"]
        )
        assert updated["status"] == "auth_expired"


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
