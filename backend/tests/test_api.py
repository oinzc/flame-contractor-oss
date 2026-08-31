from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    Account,
    ContractTarget,
    JobStatus,
    MessageTemplate,
    SendAttempt,
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


def test_patch_account_rename(client):
    """账号改名：成功改名；与其他账号重名 409；改回自身现名不算冲突。"""
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    other = client.post("/api/accounts", json={"displayName": "小号"}).json()

    renamed = client.patch(f"/api/accounts/{account['id']}", json={"displayName": "大号"})
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["displayName"] == "大号"
    assert body["enabled"] is True  # 其他字段不变

    dup = client.patch(f"/api/accounts/{account['id']}", json={"displayName": "小号"})
    assert dup.status_code == 409

    self_name = client.patch(f"/api/accounts/{account['id']}", json={"displayName": "大号"})
    assert self_name.status_code == 200

    blank = client.patch(f"/api/accounts/{other['id']}", json={"displayName": "   "})
    assert blank.status_code == 422


def test_delete_account(client, engine, settings):
    """无引用删除成功并清理浏览器档案；有好友/任务引用时 409。"""
    from datetime import date as date_cls

    from sqlmodel import Session

    from app.execution.profile_store import ensure_profile_dir
    from app.models import SendJob

    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    profile = ensure_profile_dir(settings, account["id"])

    template = client.post("/api/templates", json={"name": "T", "content": "续火"}).json()
    bound = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": template["id"]},
    ).json()
    blocked = client.delete(f"/api/accounts/{account['id']}")
    assert blocked.status_code == 409
    client.delete(f"/api/targets/{bound['id']}")

    # 同账号当日发送任务也会阻止删除
    target = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友B"},
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
    blocked_by_job = client.delete(f"/api/accounts/{account['id']}")
    assert blocked_by_job.status_code == 409
    # 任务是好友的引用，先删任务才能删好友
    with Session(engine) as session:
        job = session.exec(select(SendJob)).one()
        session.delete(job)
        session.commit()
    client.delete(f"/api/targets/{target['id']}")

    removed = client.delete(f"/api/accounts/{account['id']}")
    assert removed.status_code == 204, removed.text
    listing = client.get("/api/accounts").json()
    assert [a["displayName"] for a in listing] == []
    assert not profile.exists()  # 浏览器档案目录已清理
    assert client.delete(f"/api/accounts/{account['id']}").status_code == 404


def test_delete_account_cascades_schedules(client, engine):
    """SendSchedule 是调度器每日派生数据，不拦截删除，随账号级联清除。"""
    from datetime import date as date_cls

    from sqlmodel import Session

    from app.models import SendSchedule

    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    with Session(engine) as session:
        session.add(SendSchedule(account_id=account["id"], business_date=date_cls.today()))
        session.commit()

    removed = client.delete(f"/api/accounts/{account['id']}")
    assert removed.status_code == 204, removed.text
    with Session(engine) as session:
        assert session.exec(select(SendSchedule)).all() == []


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


def test_manual_job_review(client, engine):
    """unknown 任务人工闭环：确认已发对齐成功路径；放行回到 SCHEDULED；非 unknown 409。"""
    from datetime import date as date_cls

    from sqlmodel import Session

    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    template = client.post("/api/templates", json={"name": "T", "content": "续火"}).json()
    target = client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "好友A", "templateId": template["id"]},
    ).json()
    streak_before = target["streakDays"]

    def make_unknown_job(slot: str) -> str:
        with Session(engine) as session:
            job = SendJob(
                account_id=account["id"],
                target_id=target["id"],
                template_id=template["id"],
                business_date=date_cls.today(),
                message_slot_id=slot,
                status=JobStatus.UNKNOWN_REQUIRES_REVIEW,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            return job.id

    job_id = make_unknown_job("slot-manual")
    confirmed = client.post(f"/api/jobs/{job_id}/confirm-sent")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "succeeded"
    assert body["confirmedAt"] is not None

    with Session(engine) as session:
        t = session.get(ContractTarget, target["id"])
        assert t.streak_days == streak_before + 1  # 人工确认与自动成功一样给火花 +1
        a = session.get(Account, account["id"])
        assert a.last_success_at is not None
        attempts = session.exec(
            select(SendAttempt).where(SendAttempt.job_id == job_id)
        ).all()
        assert [x.stage for x in attempts] == ["manual_confirm_sent"]  # 动作留痕可审计

    # 已处理过的任务不能再次人工操作
    conflict = client.post(f"/api/jobs/{job_id}/confirm-sent")
    assert conflict.status_code == 409
    conflict_release = client.post(f"/api/jobs/{job_id}/release")
    assert conflict_release.status_code == 409

    job_id2 = make_unknown_job("slot-manual-2")
    released = client.post(f"/api/jobs/{job_id2}/release")
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "scheduled"
    assert released.json()["confirmedAt"] is None

    with Session(engine) as session:
        t2 = session.get(ContractTarget, target["id"])
        assert t2.streak_days == streak_before + 1  # 放行不动火花，等真正重发成功再说
        attempts2 = session.exec(
            select(SendAttempt).where(SendAttempt.job_id == job_id2)
        ).all()
        assert [x.stage for x in attempts2] == ["manual_release"]

    missing = client.post("/api/jobs/not-a-real-id/release")
    assert missing.status_code == 404


def test_batch_create_targets(client):
    """批量添加好友：新增加入 created，重名进 duplicates，整批不因单条失败中断。"""
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    client.post(
        "/api/targets",
        json={"accountId": account["id"], "nicknameSnapshot": "已有好友"},
    )

    result = client.post(
        "/api/targets/batch",
        json={
            "accountId": account["id"],
            "items": [
                {"nickname": "新好友A", "streakDays": 955},
                {"nickname": "已有好友"},
                {"nickname": "新好友B"},
                {"nickname": "  "},
                {"nickname": "新好友A"},
            ],
        },
    )
    assert result.status_code == 201, result.text
    body = result.json()
    assert body["created"] == ["新好友A", "新好友B"]  # 空白行被剔除；同批重复也按重复算
    assert body["duplicates"] == ["已有好友", "新好友A"]
    assert body["failed"] == []

    listing = client.get("/api/targets", params={"accountId": account["id"]}).json()
    assert len(listing) == 3
    seeded = next(t for t in listing if t["nicknameSnapshot"] == "新好友A")
    assert seeded["streakDays"] == 955  # 扫描读到的官方火花天数随导入回填

    missing_account = client.post(
        "/api/targets/batch", json={"accountId": "ghost", "items": [{"nickname": "x"}]}
    )
    assert missing_account.status_code == 404

    empty = client.post(
        "/api/targets/batch", json={"accountId": account["id"], "items": [{"nickname": "  "}]}
    )
    assert empty.status_code == 422


def test_batch_target_persists_and_refreshes_official_spark_state(client):
    """扫描导入保存官方徽标；重新导入已有好友只刷新展示快照，不改本地累计天数。"""
    account = client.post("/api/accounts", json={"displayName": "主账号"}).json()
    existing = client.post(
        "/api/targets",
        json={
            "accountId": account["id"],
            "nicknameSnapshot": "已有好友",
            "streakDays": 42,
        },
    ).json()

    imported = client.post(
        "/api/targets/batch",
        json={
            "accountId": account["id"],
            "items": [
                {
                    "nickname": "重燃好友",
                    "sparkKind": "normal",
                    "sparkLabel": "重燃中 1/3",
                }
            ],
        },
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["created"] == ["重燃好友"]
    assert imported.json()["refreshed"] == []

    listing = client.get("/api/targets", params={"accountId": account["id"]}).json()
    rekindle = next(item for item in listing if item["nicknameSnapshot"] == "重燃好友")
    assert rekindle["streakDays"] == 0
    assert rekindle["sparkKind"] == "normal"
    assert rekindle["sparkLabel"] == "重燃中 1/3"
    assert rekindle["sparkScannedAt"] is not None

    refreshed = client.post(
        "/api/targets/batch",
        json={
            "accountId": account["id"],
            "items": [
                {
                    "nickname": "已有好友",
                    "streakDays": 999,
                    "sparkKind": "normal",
                    "sparkLabel": "6 天后消失",
                }
            ],
        },
    )
    assert refreshed.status_code == 201, refreshed.text
    assert refreshed.json()["created"] == []
    assert refreshed.json()["refreshed"] == ["已有好友"]
    assert refreshed.json()["duplicates"] == []

    updated = client.get("/api/targets", params={"accountId": account["id"]}).json()
    existing_after = next(item for item in updated if item["id"] == existing["id"])
    assert existing_after["streakDays"] == 42
    assert existing_after["sparkLabel"] == "6 天后消失"
    assert existing_after["sparkScannedAt"] is not None
