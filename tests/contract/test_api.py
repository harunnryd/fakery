from datetime import UTC, datetime

from fastapi.testclient import TestClient

from twin.api.deps import get_bot_service, get_redis
from twin.bots.models import BotRun
from twin.bots.service import BotService
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
