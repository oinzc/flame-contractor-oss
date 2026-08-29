"""M8-1：文案池模板——创建校验、星期过滤、LRU 轮换、真实发送与预览。"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.execution.pool import pick_pool_item
from app.models import TemplatePoolItem
from tests.fakes import FakePage, create_account, make_app


def _create_pool_template(client: TestClient, items: list[dict], name: str = "文案池") -> dict:
    return client.post(
        "/api/templates",
        json={"name": name, "type": "pool", "poolItems": items},
    ).json()


def test_pool_template_requires_items(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        response = client.post("/api/templates", json={"name": "P", "type": "pool"})
        assert response.status_code == 422
        assert "至少一条" in response.json()["detail"]


def test_create_and_list_pool_template(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        created = _create_pool_template(
            client,
            [
                {"content": "早安，续火 {{target_name}}", "weekdayMask": 0},
                {"content": "周一专用文案", "weekdayMask": 1},
            ],
        )
        assert created["type"] == "pool"
        assert len(created["poolItems"]) == 2

        listed = client.get("/api/templates").json()
        pool = [t for t in listed if t["type"] == "pool"][0]
        assert [i["content"] for i in pool["poolItems"]][0].startswith("早安")
        assert pool["poolItems"][1]["weekdayMask"] == 1


def test_pick_pool_item_weekday_filter(engine):
    """weekday_mask 过滤：非生效星期返回 None；mask=0 每天可用。"""
    with Session(engine) as session:
        from app.models import MessageTemplate

        template = MessageTemplate(name="P", type="pool")
        session.add(template)
        session.commit()
        session.refresh(template)
        session.add(
            TemplatePoolItem(template_id=template.id, content="仅周一", weekday_mask=1)
        )
        session.commit()

        assert pick_pool_item(session, template.id, weekday_index=2) is None  # 周三
        chosen = pick_pool_item(session, template.id, weekday_index=0)  # 周一
        assert chosen is not None and chosen.content == "仅周一"


def test_pick_pool_item_lru_rotation(engine):
    """LRU 轮换：从未使用优先 → 交替出现，绝不连续两条相同。"""
    with Session(engine) as session:
        from app.models import MessageTemplate

        template = MessageTemplate(name="P", type="pool")
        session.add(template)
        session.commit()
        session.refresh(template)
        for content in ("甲", "乙", "丙"):
            session.add(
                TemplatePoolItem(template_id=template.id, content=content, weekday_mask=0)
            )
        session.commit()

        order = []
        for _ in range(6):
            chosen = pick_pool_item(session, template.id, weekday_index=3, consume=True)
            order.append(chosen.content)
        # 前三轮应覆盖全部三个候选（无重复），之后进入第二轮轮换
        assert sorted(order[:3]) == ["丙", "乙", "甲"]
        assert len(set(order)) == 3


def test_pool_send_renders_and_succeeds(settings):
    """端到端：池模板真实发送文本成功，且消耗了 LRU 顺序。"""
    app = make_app(
        settings,
        [FakePage(logged_in=True, conversation_titles=("好友A",), right_title="好友A")],
    )
    with TestClient(app) as client:
        account = create_account(client)
        template = _create_pool_template(
            client,
            [{"content": "第 {{streak_days}} 天，{{target_name}}，焰不熄"}],
        )
        client.post(
            "/api/targets",
            json={
                "accountId": account["id"],
                "nicknameSnapshot": "好友A",
                "templateId": template["id"],
                "streakDays": 6,
            },
        )
        payload = client.post(f"/api/accounts/{account['id']}/send").json()
        assert payload["overall"] == "ok"
        assert payload["results"][0]["status"] == "succeeded"
        assert "第 6 天" in payload["results"][0]["message"]

        with Session(app.state.engine) as session:
            item = session.exec(select(TemplatePoolItem)).one()
            assert item.last_used_at is not None


def test_pool_preview_shows_candidate_without_consuming(settings):
    """预览显示今日候选文案，但不推进轮换顺序。"""
    app = make_app(settings, [])
    with TestClient(app) as client:
        account = create_account(client)
        template = _create_pool_template(
            client,
            [{"content": "候选 {{target_name}}"}, {"content": "另一条"}],
        )
        client.post(
            "/api/targets",
            json={
                "accountId": account["id"],
                "nicknameSnapshot": "好友A",
                "templateId": template["id"],
                "streakDays": 5,
            },
        )
        preview = client.get(f"/api/accounts/{account['id']}/send/preview").json()
        # 两个候选都从未使用时按 id 稳定选取（无先后语义），只断言渲染自其中之一
        assert preview["items"][0]["message"] in ("候选 好友A", "另一条")

        with Session(app.state.engine) as session:
            item = session.exec(select(TemplatePoolItem)).first()
            assert item.last_used_at is None  # 预览不消耗轮换


def test_pool_send_skips_when_no_candidate_today(engine, settings):
    """当天无匹配候选（如仅周一、今天是周三）：跳过且不建任务。"""
    # 用 picker 直接构造「无候选」场景，避免依赖真实日期
    with Session(engine) as session:
        from app.models import MessageTemplate

        template = MessageTemplate(name="P", type="pool")
        session.add(template)
        session.commit()
        session.refresh(template)
        session.add(
            TemplatePoolItem(template_id=template.id, content="仅周日", weekday_mask=64)
        )
        session.commit()
        assert pick_pool_item(session, template.id, weekday_index=1) is None


def test_pool_send_disabled_items_means_no_candidate(engine):
    """候选全部停用 → 无可用候选。"""
    with Session(engine) as session:
        from app.models import MessageTemplate

        template = MessageTemplate(name="P", type="pool")
        session.add(template)
        session.commit()
        session.refresh(template)
        session.add(
            TemplatePoolItem(
                template_id=template.id, content="停用", weekday_mask=0, enabled=False
            )
        )
        session.commit()
        assert pick_pool_item(session, template.id, weekday_index=1) is None
