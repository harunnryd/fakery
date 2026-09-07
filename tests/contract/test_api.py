from datetime import UTC, datetime

from fastapi.testclient import TestClient

from twin.api.deps import get_bot_service, get_redis, get_subscription_service
from twin.bots.models import BotRun, WebhookSubscription
from twin.bots.service import BotService, SubscriptionService
from twin.main import create_app


class FakeRepo:
    def __init__(self) -> None:
        self.runs: dict[str, BotRun] = {}
        self._segments: dict[str, list] = {}

    async def add(self, run: BotRun) -> None:
        self.runs[run.id] = run

    async def get(self, bot_id: str) -> BotRun | None:
        return self.runs.get(bot_id)

    async def segments(self, bot_id: str) -> list:
        return self._segments.get(bot_id, [])

    async def add_segment(self, segment) -> None:
        self._segments.setdefault(segment.bot_run_id, []).append(segment)


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
