from datetime import UTC, datetime

from fastapi.testclient import TestClient

from twin.api.deps import get_bot_service, get_redis, get_subscription_service, require_api_key
from twin.bots.models import BotRun, WebhookSubscription
from twin.bots.service import BotService, SubscriptionService
from twin.core.config import Settings
from twin.main import create_app


class FakeRepo:
    def __init__(self) -> None:
        self.runs: dict[str, BotRun] = {}
        self._segments: dict[str, list] = {}
        self._notes: dict = {}

    async def add(self, run: BotRun) -> None:
        self.runs[run.id] = run

    async def get(self, bot_id: str) -> BotRun | None:
        return self.runs.get(bot_id)

    async def segments(self, bot_id: str) -> list:
        return self._segments.get(bot_id, [])

    async def add_segment(self, segment) -> None:
        self._segments.setdefault(segment.bot_run_id, []).append(segment)

    async def save_notes(self, note) -> None:
        self._notes[note.bot_run_id] = note

    async def get_notes(self, bot_id: str):
        return self._notes.get(bot_id)

    async def list_runs(self, limit: int, cursor: str | None) -> list[BotRun]:
        ordered = sorted(self.runs.values(), key=lambda run: (run.created_at, run.id), reverse=True)
        if cursor is not None and cursor in self.runs:
            anchor = self.runs[cursor]
            ordered = [
                run for run in ordered if (run.created_at, run.id) < (anchor.created_at, anchor.id)
            ]
        return ordered[:limit]

    async def annotate_speakers(self, bot_id: str, start_ms: int, end_ms: int, speaker: str) -> int:
        return 0


class FakeSubscriptions:
    def __init__(self) -> None:
        self.items: dict[str, WebhookSubscription] = {}

    async def add(self, subscription: WebhookSubscription) -> None:
        self.items[subscription.id] = subscription

    async def list(self) -> list[WebhookSubscription]:
        return list(self.items.values())

    async def remove(self, subscription_id: str) -> WebhookSubscription | None:
        return self.items.pop(subscription_id, None)


class FakeRedis:
    async def xgroup_create(self, *args: object, **kwargs: object) -> bool:
        return True

    async def xadd(self, *args: object, **kwargs: object) -> str:
        return "0-0"


def _client_with(service: BotService) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_bot_service] = lambda: service
    app.dependency_overrides[get_redis] = lambda: FakeRedis()
    app.dependency_overrides[require_api_key] = lambda: None
    return TestClient(app)


def test_healthz_reports_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_bot_returns_queued_resource() -> None:
    fixed = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    with _client_with(BotService(FakeRepo(), clock=lambda: fixed)) as client:
        response = client.post(
            "/v1/bots",
            json={"meeting_url": "https://meet.google.com/abc-defg-hij"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["id"].startswith("bot_")
    assert body["status"] == "queued"
    assert body["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert body["created_at"] == "2026-09-06T12:00:00Z"
    assert body["joined_at"] is None


def test_create_bot_without_url_fails_as_validation_problem() -> None:
    with _client_with(BotService(FakeRepo())) as client:
        response = client.post("/v1/bots", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("/validation-failed")
    assert "meeting_url" in body["detail"]


def test_get_unknown_bot_returns_not_found_problem() -> None:
    with _client_with(BotService(FakeRepo())) as client:
        response = client.get("/v1/bots/bot_missing")
    assert response.status_code == 404
    body = response.json()
    assert body["type"].endswith("/not-found")
    assert body["title"] == "Resource not found"
    assert body["status"] == 404


def test_transcript_of_known_bot_starts_empty() -> None:
    service = BotService(FakeRepo())
    with _client_with(service) as client:
        created = client.post(
            "/v1/bots",
            json={"meeting_url": "https://meet.google.com/abc-defg-hij"},
        ).json()
        response = client.get(f"/v1/bots/{created['id']}/transcript")
    assert response.status_code == 200
    assert response.json() == {"bot_id": created["id"], "segments": []}


def _webhook_client() -> TestClient:
    app = create_app()
    subscriptions = SubscriptionService(FakeSubscriptions())
    app.dependency_overrides[get_bot_service] = lambda: BotService(FakeRepo())
    app.dependency_overrides[get_redis] = lambda: FakeRedis()
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[get_subscription_service] = lambda: subscriptions
    return TestClient(app)


def test_webhook_lifecycle() -> None:
    with _webhook_client() as client:
        created = client.post("/v1/webhooks", json={"url": "https://client.example/hook"}).json()
        assert created["id"].startswith("wh_")
        listed = client.get("/v1/webhooks").json()
        assert [sub["id"] for sub in listed] == [created["id"]]
        deleted = client.delete(f"/v1/webhooks/{created['id']}")
        assert deleted.status_code == 204
        assert client.get("/v1/webhooks").json() == []


def test_webhook_rejects_bad_url_as_validation_problem() -> None:
    with _webhook_client() as client:
        response = client.post("/v1/webhooks", json={"url": "not-a-url"})
    assert response.status_code == 422
    assert response.json()["type"].endswith("/validation-failed")


def test_webhook_delete_unknown_returns_not_found_problem() -> None:
    with _webhook_client() as client:
        response = client.delete("/v1/webhooks/wh_missing")
    assert response.status_code == 404
    assert response.json()["type"].endswith("/not-found")


def _authed_client() -> TestClient:
    service = BotService(FakeRepo())
    app = create_app()
    app.dependency_overrides[get_bot_service] = lambda: service
    app.dependency_overrides[get_redis] = lambda: FakeRedis()
    return TestClient(app)


def test_api_key_guards_v1_routes() -> None:
    with _authed_client() as client:
        client.app.state.settings = Settings(api_key="secret")
        denied = client.post(
            "/v1/bots", json={"meeting_url": "https://meet.google.com/abc-defg-hij"}
        )
        assert denied.status_code == 401
        assert denied.json()["type"].endswith("/unauthorized")
        allowed = client.post(
            "/v1/bots",
            json={"meeting_url": "https://meet.google.com/abc-defg-hij"},
            headers={"x-api-key": "secret"},
        )
        assert allowed.status_code == 202
        assert client.get("/healthz").status_code == 200


def test_request_id_header_present() -> None:
    service = BotService(FakeRepo())
    with _client_with(service) as client:
        response = client.get("/healthz", headers={"x-request-id": "req_9"})
        assert response.headers["x-request-id"] == "req_9"
        generated = client.get("/healthz")
        assert generated.headers["x-request-id"].startswith("req_")


def test_list_bots_paginates_by_cursor() -> None:
    service = BotService(FakeRepo())
    with _client_with(service) as client:
        for _ in range(3):
            client.post("/v1/bots", json={"meeting_url": "https://meet.google.com/abc-defg-hij"})
        first = client.get("/v1/bots?limit=2").json()
        assert len(first["bots"]) == 2
        assert first["next_cursor"] == first["bots"][1]["id"]
        second = client.get(f"/v1/bots?limit=2&cursor={first['next_cursor']}").json()
        assert len(second["bots"]) == 1
        assert second["next_cursor"] is None


async def test_notes_roundtrip() -> None:
    from twin.notes.summarizer import ActionItem, MeetingNotes

    repo = FakeRepo()
    service = BotService(repo)
    app = create_app()
    app.dependency_overrides[get_bot_service] = lambda: service
    app.dependency_overrides[get_redis] = lambda: FakeRedis()
    app.dependency_overrides[require_api_key] = lambda: None
    with TestClient(app) as client:
        created = client.post(
            "/v1/bots", json={"meeting_url": "https://meet.google.com/abc-defg-hij"}
        ).json()
        assert client.get(f"/v1/bots/{created['id']}/notes").status_code == 404
        await service.store_notes(
            created["id"],
            MeetingNotes(
                summary="standup",
                key_points=["rilis"],
                action_items=[ActionItem(text="kirim", owner="Dina", due=None)],
            ),
        )
        notes = client.get(f"/v1/bots/{created['id']}/notes").json()
        assert notes["summary"] == "standup"
        assert notes["action_items"] == [{"text": "kirim", "owner": "Dina", "due": None}]
