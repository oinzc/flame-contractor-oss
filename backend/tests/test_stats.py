"""M8-2：统计聚合（按日/连续成功/成功率）与 CSV 导出。"""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import JobStatus, SendJob
from tests.fakes import create_account, make_app


def _seed_job(engine, account_id: str, target_id: str, template_id: str, day: date, status: JobStatus) -> None:
    with Session(engine) as session:
        session.add(
            SendJob(
                account_id=account_id,
                target_id=target_id,
                template_id=template_id,
                business_date=day,
                message_slot_id=f"slot-{day.isoformat()}-{status.value}",
                status=status,
            )
        )
        session.commit()


def test_stats_summary_aggregates_and_streaks(settings):
    """按日聚合 + 连续成功天数（断档打断）+ 成功率。"""
    app = make_app(settings, [])
    with TestClient(app) as client:
        account = create_account(client)
        target = client.post(
            "/api/targets", json={"accountId": account["id"], "nicknameSnapshot": "好友A"}
        ).json()
        template = client.post(
            "/api/templates", json={"name": "T", "content": "{{target_name}}"}
        ).json()
        tid = template["id"]
        today = date.today()
        # 今天往前：今天成功、昨天成功、前天失败（打断）、大前天成功
        _seed_job(app.state.engine, account["id"], target["id"], tid, today, JobStatus.SUCCEEDED)
        _seed_job(app.state.engine, account["id"], target["id"], tid, today - timedelta(days=1), JobStatus.SUCCEEDED)
        _seed_job(app.state.engine, account["id"], target["id"], tid, today - timedelta(days=2), JobStatus.FAILED_TERMINAL)
        _seed_job(app.state.engine, account["id"], target["id"], tid, today - timedelta(days=3), JobStatus.SUCCEEDED)

        payload = client.get("/api/stats/summary").json()
        assert payload["totalJobs"] == 4
        assert payload["succeededJobs"] == 3
        assert payload["successRate"] == round(3 / 4, 4)
        # 当前连续成功 = 今天+昨天 = 2（前天失败打断）；最长 = 2
        assert payload["currentSuccessStreak"] == 2
        assert payload["bestSuccessStreak"] == 2
        daily = {row["date"]: row for row in payload["daily"]}
        assert daily[today.isoformat()]["succeeded"] == 1


def test_stats_summary_empty(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        payload = client.get("/api/stats/summary").json()
        assert payload["totalJobs"] == 0
        assert payload["successRate"] is None
        assert payload["daily"] == []


def test_stats_export_csv(settings):
    app = make_app(settings, [])
    with TestClient(app) as client:
        account = create_account(client)
        target = client.post(
            "/api/targets", json={"accountId": account["id"], "nicknameSnapshot": "好友A"}
        ).json()
        template = client.post(
            "/api/templates", json={"name": "T", "content": "{{target_name}}"}
        ).json()
        _seed_job(app.state.engine, account["id"], target["id"], template["id"], date.today(), JobStatus.UNKNOWN_REQUIRES_REVIEW)

        response = client.get("/api/stats/export.csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
        body = response.text.strip().splitlines()
        assert body[0].startswith("business_date")
        assert len(body) == 2
        assert "unknown_requires_review" in body[1]
