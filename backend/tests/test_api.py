from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models import (
    Account,
    ContractTarget,
    JobStatus,
    MessageTemplate,
    SendJob,
)


def test_health_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["app"] == "Flame Contractor"
    assert payload["database"] == "ready"


def test_create_account_returns_camel_case(client):
    response = client.post(
        "/api/accounts",
        json={
            "displayName": "主账号",
            "timezone": "Asia/Shanghai",
            "sendWindowStart": "20:00",
            "sendWindowEnd": "22:00",
        },
    )
    assert response.status_code == 201, response.text
    account = response.json()
    assert account["displayName"] == "主账号"
    assert account["status"] == "pending_login"
    assert account["enabled"] is True
    assert "display_name" not in account


def test_create_account_duplicate_name_conflicts(client):
    payload = {"displayName": "主账号"}
    assert client.post("/api/accounts", json=payload).status_code == 201
    conflict = client.post("/api/accounts", json=payload)
    assert conflict.status_code == 409


def test_create_account_rejects_credential_fields(client):
    response = client.post(
        "/api/accounts",
        json={"displayName": "坏账号", "cookie": "sessionid=secret"},
    )
    assert response.status_code == 422


def test_create_account_rejects_bad_timezone_and_window(client):
    assert client.post("/api/accounts", json={"displayName": "A", "timezone": "Mars/Olympus"}).status_code == 422
    assert (
        client.post(
            "/api/accounts",
            json={"displayName": "A", "sendWindowStart": "25:00"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/accounts",
            json={"displayName": "A", "sendWindowStart": "21:00", "sendWindowEnd": "21:00"},
        ).status_code
        == 422
    )


def test_patch_account_allows_pause_only(client):
    created = client.post("/api/accounts", json={"displayName": "主账号"}).json()

    paused = client.patch(f"/api/accounts/{created['id']}", json={"status": "paused"})
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    forbidden = client.patch(f"/api/accounts/{created['id']}", json={"status": "healthy"})
    assert forbidden.status_code == 422

    missing = client.patch("/api/accounts/not-a-real-id", json={"enabled": False})
    assert missing.status_code == 404


def test_template_validation(client):
    ok = client.post(
        "/api/templates",
        json={"name": "每日一言", "content": "{{target_name}}，第 {{streak_days}} 天，焰不可熄。"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["type"] == "text"
    assert ok.json()["version"] == 1

    unknown_var = client.post(
        "/api/templates",
        json={"name": "坏模板", "content": "{{system_exec}}"},
    )
    assert unknown_var.status_code == 422

    bad_syntax = client.post(
        "/api/templates",
        json={"name": "坏语法", "content": "hello {world}"},
    )
    assert bad_syntax.status_code == 422

    duplicate = client.post("/api/templates", json={"name": "每日一言", "content": "重复"})
    assert duplicate.status_code == 409


def test_target_requires_existing_account_and_template(client):
    missing_account = client.post(
        "/api/targets",
        json={"accountId": "ghost", "nicknameSnapshot": "好友A"},
    )
    assert missing_account.status_code == 404

    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    missing_template = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": "ghost"},
    )
    assert missing_template.status_code == 404

    created = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "streakDays": 3},
    )
    assert created.status_code == 201, created.text

    duplicate = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
    )
    assert duplicate.status_code == 409

    listing = client.get("/api/targets", params={"accountId": account["id"]})
    assert listing.status_code == 200
    assert [item["nicknameSnapshot"] for item in listing.json()] == ["好友A"]


def test_delete_target(client):
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    target = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
    ).json()

    removed = client.delete(f"/api/targets/{target['id']}")
    assert removed.status_code == 204

    listing = client.get("/api/targets", params={"accountId": account["id"]}).json()
    assert listing == []

    missing = client.delete("/api/targets/not-a-real-id")
    assert missing.status_code == 404


def test_patch_target_streak(client):
    """火花天数手动校准：只改 streakDays，其余字段不变。"""
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    target = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "streakDays": 3},
    ).json()

    updated = client.patch(
        f"/api/targets/{target['id']}", json={"streakDays": 12}
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["streakDays"] == 12
    assert body["nicknameSnapshot"] == "好友A"  # 其它字段不变

    missing = client.patch("/api/targets/not-a-real-id", json={"streakDays": 5})
    assert missing.status_code == 404

    # 负数被 422 拒绝
    negative = client.patch(f"/api/targets/{target['id']}", json={"streakDays": -1})
    assert negative.status_code == 422


def test_patch_target_enabled(client):
    """启停好友：暂停后 enabled=False，可再启用；与火花校准互不影响。"""
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    target = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
    ).json()
    assert target["enabled"] is True

    paused = client.patch(f"/api/targets/{target['id']}", json={"enabled": False})
    assert paused.status_code == 200, paused.text
    assert paused.json()["enabled"] is False

    listing = client.get("/api/targets", params={"accountId": account["id"]}).json()
    assert listing[0]["enabled"] is False

    resumed = client.patch(f"/api/targets/{target['id']}", json={"enabled": True})
    assert resumed.json()["enabled"] is True


def test_delete_target_with_jobs_conflicts(client, engine):
    from datetime import date as date_cls

    from sqlmodel import Session

    from app.models import SendJob

    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    template = client.post("/api/templates", json={"name": "T", "content": "续火"}).json()
    target = client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "好友A",
            "templateId": template["id"],
        },
    ).json()

    with Session(engine) as session:
        session.add(
            SendJob(
                account_id=account["id"],
                target_id=target["id"],
                template_id=template["id"],
                business_date=date_cls.today(),
                message_slot_id="slot",
            )
        )
        session.commit()

    blocked = client.delete(f"/api/targets/{target['id']}")
    assert blocked.status_code == 409


def test_dashboard_aggregates_real_data(client, engine):
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    template = client.post("/api/templates", json={"name": "T", "content": "续火"}).json()
    target = client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "好友A",
            "templateId": template["id"],
        },
    ).json()

    with Session(engine) as session:
        session.add(
            SendJob(
                account_id=account["id"],
                target_id=target["id"],
                template_id=template["id"],
                business_date=date.today(),
                message_slot_id="slot-1",
                status=JobStatus.SUCCEEDED,
                attempt_count=1,
            )
        )
        session.add(
            SendJob(
                account_id=account["id"],
                target_id=target["id"],
                template_id=template["id"],
                business_date=date.today(),
                message_slot_id="slot-2",
                status=JobStatus.SCHEDULED,
            )
        )
        session.commit()

    summary = client.get("/api/dashboard").json()
    assert summary["accountCount"] == 1
    assert summary["healthyAccountCount"] == 0
    assert summary["activeContractCount"] == 1
    assert summary["todaySucceeded"] == 1
    assert summary["todayPending"] == 1
    assert summary["needsAttention"] == 0
    assert len(summary["accounts"]) == 1
    assert len(summary["recentJobs"]) == 2


def test_send_job_daily_slot_is_unique(engine):
    with Session(engine) as session:
        account = Account(display_name="账号")
        template = MessageTemplate(name="模板", content="续火")
        session.add(account)
        session.add(template)
        session.commit()

        target = ContractTarget(account_id=account.id, nickname_snapshot="好友A")
        session.add(target)
        session.commit()

        def job(slot: str) -> SendJob:
            return SendJob(
                account_id=account.id,
                target_id=target.id,
                template_id=template.id,
                business_date=date.today(),
                message_slot_id=slot,
                status=JobStatus.SCHEDULED,
            )

        session.add(job("slot"))
        session.commit()

        session.add(job("slot"))
        try:
            session.commit()
            raised = False
        except IntegrityError:
            session.rollback()
            raised = True
        assert raised
