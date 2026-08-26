import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.core.database import build_engine
from app.core.migrations import run_migrations
from app.execution.browser_manager import BrowserManager
from app.execution.selectors import (
    CONVERSATION_ITEM,
    CONVERSATION_LIST,
    CONVERSATION_TITLE,
    RIGHT_TITLE,
    SEARCH_INPUT,
)
from app.main import create_app


# ---------------------------------------------------------------- fakes


class FakeElement:
    def __init__(self, text: str = ""):
        self._text = text

    async def inner_text(self) -> str:
        return self._text

    async def click(self) -> None:
        pass


class FakeItem:
    def __init__(self, title: str):
        self._title = title

    async def query_selector(self, selector: str):
        if selector == CONVERSATION_TITLE:
            return FakeElement(self._title)
        return None

    async def click(self) -> None:
        pass


class FakePage:
    def __init__(
        self,
        *,
        logged_in: bool = False,
        become_logged_after: int | None = None,
        conversation_titles: tuple[str, ...] = (),
        right_title: str | None = None,
    ):
        self.logged_in = logged_in
        self._become_logged_after = become_logged_after
        self._list_polls = 0
        self.conversation_titles = list(conversation_titles)
        self.right_title = right_title
        self.gotos: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.closed = False

    async def goto(self, url: str) -> None:
        self.gotos.append(url)

    async def query_selector(self, selector: str):
        if selector == CONVERSATION_LIST:
            if self._become_logged_after is not None:
                self._list_polls += 1
                if self._list_polls >= self._become_logged_after:
                    self.logged_in = True
            return FakeElement("") if self.logged_in else None
        if selector == RIGHT_TITLE:
            return FakeElement(self.right_title) if self.right_title is not None else None
        if selector == SEARCH_INPUT:
            return FakeElement("")
        return None

    async def query_selector_all(self, selector: str):
        if selector == CONVERSATION_ITEM:
            return [FakeItem(title) for title in self.conversation_titles]
        return []

    async def fill(self, selector: str, value: str) -> None:
        self.fills.append((selector, value))


class FakeContext:
    def __init__(self, page: FakePage):
        self.page = page

    async def close(self) -> None:
        self.page.closed = True


def make_factory(pages: list[FakePage]):
    queue = list(pages)

    async def factory(_profile_path, _headless):
        page = queue.pop(0)
        return FakeContext(page), page

    return factory


def make_app(settings, pages: list[FakePage]):
    engine = build_engine(settings.database_url)
    run_migrations(settings.database_url)
    manager = BrowserManager(
        engine,
        settings,
        browser_factory=make_factory(pages),
        login_poll_interval=0.02,
        login_timeout=1.0,
        sleep=lambda _seconds: asyncio.sleep(0),
    )
    return create_app(settings, browser_manager=manager)


def create_account(client: TestClient, name: str = "测试账号") -> dict:
    response = client.post("/api/accounts", json={"displayName": name})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- tests


def test_migrations_create_expected_tables(engine):
    tables = set(inspect(engine).get_table_names())
    assert {"account", "contract_target", "message_template", "send_job", "send_attempt"} <= tables


def test_login_flow_succeeds_and_marks_healthy(settings):
    app = make_app(settings, [FakePage(become_logged_after=2)])
    with TestClient(app) as client:
        account = create_account(client)

        started = client.post(f"/api/accounts/{account['id']}/login")
        assert started.status_code == 202, started.text
        assert started.json()["state"] == "waiting_scan"

        deadline = time.time() + 5
        state = None
        while time.time() < deadline:
            status = client.get(f"/api/accounts/{account['id']}/login").json()
            state = status["state"]
            if state == "succeeded":
                break
            time.sleep(0.05)
        assert state == "succeeded"

        accounts = client.get("/api/accounts").json()
        updated = next(item for item in accounts if item["id"] == account["id"])
        assert updated["status"] == "healthy"
        assert updated["lastVerifiedAt"] is not None


def test_login_conflict_and_cancel(settings):
    app = make_app(settings, [FakePage(become_logged_after=None)])
    with TestClient(app) as client:
        account = create_account(client)
        url = f"/api/accounts/{account['id']}/login"

        first = client.post(url)
        assert first.status_code == 202

        conflict = client.post(url)
        assert conflict.status_code == 409

        cancelled = client.post(f"{url}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "idle"

        restarted = client.post(url)
        assert restarted.status_code == 202
        client.post(f"{url}/cancel")


def test_dry_run_blocked_while_login_active(settings):
    app = make_app(
        settings,
        [FakePage(become_logged_after=None), FakePage(logged_in=True)],
    )
    with TestClient(app) as client:
        account = create_account(client)
        client.post(f"/api/accounts/{account['id']}/login")

        blocked = client.post(f"/api/accounts/{account['id']}/dry-run")
        assert blocked.status_code == 409

        client.post(f"/api/accounts/{account['id']}/login/cancel")


def test_dry_run_locating_targets_without_sending(settings):
    app = make_app(
        settings,
        [
            FakePage(
                logged_in=True,
                conversation_titles=("好友A", "好友A同学"),
                right_title="好友A",
            )
        ],
    )
    with TestClient(app) as client:
        account = create_account(client)
        for name in ("好友A", "好友A同学", "不存在"):
            response = client.post(
                "/api/targets",
                json={"accountId": account["id"], "nicknameSnapshot": name},
            )
            assert response.status_code == 201, response.text

        result = client.post(f"/api/accounts/{account['id']}/dry-run")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["overall"] == "ok"
        by_name = {item["target"]: item for item in payload["targets"]}
        assert by_name["好友A"]["status"] == "found"
        assert by_name["好友A"]["title"] == "好友A"
        assert by_name["好友A同学"]["status"] == "ambiguous"
        assert by_name["不存在"]["status"] == "not_found"

        assert payload["artifactPath"] is not None
        assert Path(payload["artifactPath"]).is_file()

        updated = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account["id"]
        )
        assert updated["lastVerifiedAt"] is not None
        assert updated["status"] == "pending_login"  # Dry Run 不改变健康状态，只更新时间戳


def test_dry_run_marks_auth_expired(settings):
    app = make_app(settings, [FakePage(logged_in=False)])
    with TestClient(app) as client:
        account = create_account(client)
        client.post(
            "/api/targets",
            json={"accountId": account["id"], "nicknameSnapshot": "好友A"},
        )

        result = client.post(f"/api/accounts/{account['id']}/dry-run").json()
        assert result["overall"] == "auth_expired"

        updated = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account["id"]
        )
        assert updated["status"] == "auth_expired"
