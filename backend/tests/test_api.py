from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

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


def test_patch_template_text(client):
    """文本模板编辑：改内容 bump version，只改名字不 bump；重名 409；清空内容 422。"""
    template = client.post(
        "/api/templates", json={"name": "每日一言", "content": "早上好"}
    ).json()

    renamed = client.patch(f"/api/templates/{template['id']}", json={"name": "早安问候"})
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "早安问候"
    assert body["content"] == "早上好"
    assert body["version"] == 1  # 只改名字不影响发送内容

    recented = client.patch(
        f"/api/templates/{template['id']}", json={"content": "晚安，{{target_name}}"}
    )
    body = recented.json()
    assert body["content"] == "晚安，{{target_name}}"
    assert body["version"] == 2  # 内容变化 → 当日幂等键随之变化

    other = client.post("/api/templates", json={"name": "晚安", "content": "晚安"}).json()
    dup = client.patch(f"/api/templates/{template['id']}", json={"name": "晚安"})
    assert dup.status_code == 409
    # 改名到自己的现名不算冲突
    self_name = client.patch(f"/api/templates/{other['id']}", json={"name": "晚安"})
    assert self_name.status_code == 200

    empty = client.patch(f"/api/templates/{other['id']}", json={"content": "   "})
    assert empty.status_code == 422  # 合并校验：文本模板内容不能为空

    missing = client.patch("/api/templates/not-a-real-id", json={"name": "X"})
    assert missing.status_code == 404


def test_patch_template_enabled(client):
    template = client.post("/api/templates", json={"name": "T", "content": "续火"}).json()
    assert template["enabled"] is True

    off = client.patch(f"/api/templates/{template['id']}", json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert off.json()["version"] == 1  # 启停不影响发送内容

    on = client.patch(f"/api/templates/{template['id']}", json={"enabled": True})
    assert on.json()["enabled"] is True


def test_patch_template_pool_replaces_items(client, engine):
    """池候选整体替换：content 相同的候选继承 last_used_at；候选变化 bump version。"""
    from datetime import datetime as dt

    from sqlmodel import Session

    from app.models import TemplatePoolItem

    template = client.post(
        "/api/templates",
        json={
            "name": "晚安池",
            "type": "pool",
            "poolItems": [
                {"content": "晚安", "weekdayMask": 0},
                {"content": "好梦", "weekdayMask": 0},
            ],
        },
    ).json()
    assert len(template["poolItems"]) == 2

    with Session(engine) as session:
        item = session.exec(
            select(TemplatePoolItem).where(TemplatePoolItem.content == "晚安")
        ).one()
        item.last_used_at = dt(2026, 8, 1, 12, 0, 0)
        session.add(item)
        session.commit()

    updated = client.patch(
        f"/api/templates/{template['id']}",
        json={
            "poolItems": [
                {"content": "晚安", "weekdayMask": 62},
                {"content": "做个好梦", "weekdayMask": 0},
                {"content": "早点休息", "weekdayMask": 0},
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert [i["content"] for i in body["poolItems"]] == ["晚安", "做个好梦", "早点休息"]
    assert body["poolItems"][0]["weekdayMask"] == 62
    assert body["version"] == 2

    with Session(engine) as session:
        kept = session.exec(
            select(TemplatePoolItem).where(TemplatePoolItem.content == "晚安")
        ).one()
        fresh = session.exec(
            select(TemplatePoolItem).where(TemplatePoolItem.content == "做个好梦")
        ).one()
        assert kept.last_used_at is not None  # 同名候选继承轮换时间
        assert fresh.last_used_at is None

    # 仅把候选改成同一份内容不 bump version
    same = client.patch(
        f"/api/templates/{template['id']}",
        json={
            "poolItems": [
                {"content": "晚安", "weekdayMask": 62},
                {"content": "做个好梦", "weekdayMask": 0},
                {"content": "早点休息", "weekdayMask": 0},
            ]
        },
    )
    assert same.json()["version"] == 2

    # 清空候选 → 合并校验拒绝
    cleared = client.patch(f"/api/templates/{template['id']}", json={"poolItems": []})
    assert cleared.status_code == 422


def test_delete_template(client, engine):
    """无引用可删（池候选一并删除）；被好友绑定或任务引用时 409。"""
    from datetime import date as date_cls

    from sqlmodel import Session

    from app.models import SendJob, TemplatePoolItem

    pool = client.post(
        "/api/templates",
        json={"name": "池", "type": "pool", "poolItems": [{"content": "嗨", "weekdayMask": 0}]},
    ).json()
    removed = client.delete(f"/api/templates/{pool['id']}")
    assert removed.status_code == 204
    with Session(engine) as session:
        leftover = session.exec(
            select(TemplatePoolItem).where(TemplatePoolItem.template_id == pool["id"])
        ).all()
        assert leftover == []  # 池候选随模板一起删除
    assert client.delete(f"/api/templates/{pool['id']}").status_code == 404

    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    template = client.post("/api/templates", json={"name": "T", "content": "续火"}).json()

    bound = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": template["id"]},
    ).json()
    blocked_by_target = client.delete(f"/api/templates/{template['id']}")
    assert blocked_by_target.status_code == 409
    client.delete(f"/api/targets/{bound['id']}")

    job_template = client.post("/api/templates", json={"name": "T2", "content": "在吗"}).json()
    target = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友B"},
    ).json()
    with Session(engine) as session:
        session.add(
            SendJob(
                account_id=account["id"],
                target_id=target["id"],
                template_id=job_template["id"],
                business_date=date_cls.today(),
                message_slot_id="slot",
            )
        )
        session.commit()
    blocked_by_job = client.delete(f"/api/templates/{job_template['id']}")
    assert blocked_by_job.status_code == 409


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
