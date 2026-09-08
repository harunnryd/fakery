from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from twin.bots.models import TranscriptSegment, WebhookDelivery
from twin.webhooks.dispatch import (
    DbOutbox,
    WebhookSender,
    segment_payload,
    status_payload,
)


class FakeRows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> list:
        return self._rows


class FakeSession:
    store: dict[str, object] = {}
    deleted: list[str] = []

    def __init__(self, rows: list, aged: list | None = None) -> None:
        self._rows = rows
        self._aged = aged if aged is not None else []
        self.commits = 0

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, stmt: object) -> FakeRows:
        text = str(stmt)
        if "next_try_at <=" in text:
            return FakeRows(self._rows)
        if "webhook_deliveries" in text:
            return FakeRows(self._aged)
        return FakeRows(self._rows)

    async def get(self, model: object, key: str):
        return FakeSession.store.get(key)

    def add(self, row: object) -> None:
        FakeSession.store[row.id] = row

    async def delete(self, row: object) -> None:
        FakeSession.deleted.append(row.id)

    async def commit(self) -> None:
        self.commits += 1


class FakeSessionFactory:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def __call__(self):  # type: ignore[no-untyped-def]
        return FakeSession(self._rows)


class FakeRedis:
    def __init__(self) -> None:
        self.held: set[str] = set()

    async def set(self, name: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and name in self.held:
            return None
        self.held.add(name)
        return True

    async def get(self, name: str) -> str | None:
        return name if name in self.held else None

    async def delete(self, name: str) -> None:
        self.held.discard(name)


class FlakyTransport(httpx.AsyncBaseTransport):
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if len(self.calls) <= self.failures:
            return httpx.Response(500, request=request)
        return httpx.Response(200, request=request)


def _urls(*urls: str):
    return [SimpleNamespace(url=url) for url in urls]


def _delivery(
    key: str = "whd_1",
    bot_id: str = "bot_1",
    attempts: int = 0,
    age_days: int = 0,
    due: bool = True,
) -> WebhookDelivery:
    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    return WebhookDelivery(
        id=key,
        url="https://client.example/hook",
        payload={"type": "bot.status_changed", "bot_id": bot_id, "event_id": "evt_1"},
        attempts=attempts,
        next_try_at=now - timedelta(seconds=1 if due else -3600),
        created_at=now - timedelta(days=age_days),
    )


def _segment() -> TranscriptSegment:
    moment = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    return TranscriptSegment(
        id="seg_1",
        bot_run_id="bot_1",
        speaker=None,
        text="setuju",
        start_ms=1000,
        end_ms=2000,
        created_at=moment,
    )


@pytest.fixture(autouse=True)
def _clean_store():
    FakeSession.store = {}
    FakeSession.deleted = []
    yield
    FakeSession.store = {}
    FakeSession.deleted = []


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (lambda: status_payload("bot_1", "recording", "evt_1"), ("bot.status_changed", "bot_1")),
        (lambda: segment_payload(_segment(), "evt_1"), ("transcript.segment", "bot_1")),
    ],
    ids=["status", "segment"],
)
def test_payload_envelopes(builder, expected: object) -> None:
    payload = builder()
    kind, bot_id = expected  # type: ignore[misc]
    assert payload["type"] == kind
    assert payload["bot_id"] == bot_id
    assert payload["event_id"] == "evt_1"


async def test_outbox_enqueues_per_subscriber() -> None:
    outbox = DbOutbox(FakeSessionFactory(_urls("https://a.example", "https://b.example")))
    await outbox.status_changed("bot_1", "recording")
    assert len(FakeSession.store) == 2


async def test_outbox_silent_without_subscribers() -> None:
    outbox = DbOutbox(FakeSessionFactory([]))
    await outbox.status_changed("bot_1", "recording")
    assert FakeSession.store == {}


@pytest.mark.parametrize(
    ("failures", "attempts", "expected"),
    [(0, 0, "sent"), (9, 0, "failed"), (9, 4, "dead")],
    ids=["first-try", "backs-off", "dead-letters"],
)
async def test_sender_attempt_outcomes(failures: int, attempts: int, expected: str) -> None:
    transport = FlakyTransport(failures=failures)
    client = httpx.AsyncClient(transport=transport)
    item = _delivery(attempts=attempts)
    FakeSession.store = {item.id: item}
    sender = WebhookSender(FakeSessionFactory([item]), "secret", client)
    summary = await sender.run_once(FakeRedis())
    assert len(transport.calls) == 1
    assert summary[expected] == 1
    if expected == "failed":
        assert item.next_try_at > datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        assert item.id not in FakeSession.deleted
    else:
        assert item.id in FakeSession.deleted


async def test_sender_signs_body() -> None:
    from twin.webhooks.signing import sign

    transport = FlakyTransport(failures=0)
    client = httpx.AsyncClient(transport=transport)
    item = _delivery()
    FakeSession.store = {item.id: item}
    sender = WebhookSender(FakeSessionFactory([item]), "secret", client)
    await sender.run_once(FakeRedis())
    request = transport.calls[0]
    timestamp = request.headers["X-Webhook-Timestamp"]
    assert request.headers["X-Webhook-Signature"] == sign("secret", request.content, timestamp)


async def test_sender_skips_locked_bot() -> None:
    transport = FlakyTransport(failures=0)
    client = httpx.AsyncClient(transport=transport)
    item = _delivery()
    sender = WebhookSender(FakeSessionFactory([item]), "secret", client)
    redis = FakeRedis()
    redis.held.add("webhooks:send:bot_1")
    summary = await sender.run_once(redis)
    assert summary == {"sent": 0, "failed": 0, "dead": 0, "purged": 0}
    assert transport.calls == []


async def test_sender_purges_old_rows() -> None:
    transport = FlakyTransport(failures=0)
    client = httpx.AsyncClient(transport=transport)
    old = _delivery(key="whd_old", age_days=40, due=False)
    FakeSession.store = {old.id: old}
    aged_session = FakeSession([], aged=[old])

    class AgedFactory(FakeSessionFactory):
        def __call__(self):  # type: ignore[no-untyped-def]
            return aged_session

    sender = WebhookSender(AgedFactory([]), "secret", client)
    summary = await sender.run_once(FakeRedis())
    assert summary["purged"] == 1
    assert old.id in FakeSession.deleted
