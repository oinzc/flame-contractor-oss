"""M8-3：备份导出/导入——全量往返、空库恢复、幂等 upsert。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import Account, ContractTarget, SendJob, TemplatePoolItem
from tests.fakes import FakePage, create_account, make_app


def _seed_and_export(client: TestClient) -> dict:
    account = create_account(client)
    template = client.post(
        "/api/templates",
        json={
            "name": "池",
            "type": "pool",
            "poolItems": [{"content": "候选 {{target_name}}"}],
        },
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
    client.post(f"/api/accounts/{account['id']}/send")
    return client.get("/api/backup").json()


def test_backup_roundtrip_to_fresh_db(settings):
    """导出 → 空库导入 = 完整恢复（含文案池候选与任务记录）。"""
    app = make_app(
        settings,
        [FakePage(logged_in=True, conversation_titles=("好友A",), right_title="好友A")],
    )
    with TestClient(app) as client:
        exported = _seed_and_export(client)

    assert exported["schemaVersion"] == 1
    data = exported["data"]
    assert len(data["accounts"]) == 1
    assert len(data["targets"]) == 1
    assert len(data["poolItems"]) == 1
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["status"] == "succeeded"

    # 空库导入 = 完整恢复
    fresh_settings = settings.model_copy(
        update={"database_url": settings.database_url.replace("test.db", "fresh.db")}
    )
    app2 = make_app(fresh_settings, [])
    with TestClient(app2) as client2:
        result = client2.post("/api/backup/import", json=exported).json()
        assert result["inserted"]["accounts"] == 1
        assert result["inserted"]["pool_items"] == 1
        assert result["inserted"]["jobs"] == 1

        with Session(app2.state.engine) as session:
            assert len(session.exec(select(Account)).all()) == 1
            assert len(session.exec(select(TemplatePoolItem)).all()) == 1
            assert len(session.exec(select(SendJob)).all()) == 1
            job = session.exec(select(SendJob)).one()
            assert job.status.value == "succeeded"
            target = session.exec(select(ContractTarget)).one()
            # 种子阶段真实发送成功，火花已 5→6；导入应完整还原导出值
            assert target.streak_days == data["targets"][0]["streak_days"] == 6


def test_backup_import_is_idempotent_upsert(settings):
    """同一份备份导入两次：第二次全部走更新（updated），数据不重复。"""
    app = make_app(settings, [])
    with TestClient(app) as client:
        create_account(client)
        exported = client.get("/api/backup").json()

        first = client.post("/api/backup/import", json=exported).json()
        second = client.post("/api/backup/import", json=exported).json()
        # 同库导入：已存在的行全部走更新（upsert），绝不产生重复
        assert first["inserted"]["accounts"] == 0
        assert first["updated"]["accounts"] == 1
        assert second["inserted"]["accounts"] == 0
        assert second["updated"]["accounts"] == 1

        with Session(app.state.engine) as session:
            assert len(session.exec(select(Account)).all()) == 1


def test_backup_rejects_unknown_schema_version(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        create_account(client)
        exported = client.get("/api/backup").json()
        exported["schemaVersion"] = 999
        response = client.post("/api/backup/import", json=exported)
        assert response.status_code == 422
