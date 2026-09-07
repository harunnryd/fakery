from datetime import UTC, datetime

import httpx
import pytest

from twin.bots.models import TranscriptSegment
from twin.webhooks.dispatch import (
    WebhookDispatcher,
    segment_payload,
    status_payload,
)
from twin.webhooks.signing import sign


class FakeSessionFactory:
    def __init__(self, urls: list[str]) -> None:
        self._urls = urls

    def __call__(self):  # type: ignore[no-untyped-def]
        return _FakeSession(self._urls)


class _FakeRows:
    def __init__(self, urls: list[str]) -> None:
        self._urls = urls

    def scalars(self) -> list:
        from types import SimpleNamespace

        return [SimpleNamespace(url=url) for url in self._urls]


class _FakeSession:
    def __init__(self, urls: list[str]) -> None:
        self._urls = urls

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, stmt: object) -> _FakeRows:
        return _FakeRows(self._urls)


class FlakyTransport(httpx.AsyncBaseTransport):
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if len(self.calls) <= self.failures:
            return httpx.Response(500, request=request)
        return httpx.Response(200, request=request)


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


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (lambda: status_payload("bot_1", "recording"), ("bot.status_changed", "bot_1")),
        (lambda: segment_payload(_segment()), ("transcript.segment", "bot_1")),
    ],
    ids=["status", "segment"],
)
def test_payload_envelopes(builder, expected: object) -> None:
    payload = builder()
    kind, bot_id = expected  # type: ignore[misc]
    assert payload["type"] == kind
    assert payload["bot_id"] == bot_id


@pytest.mark.parametrize(
    ("failures", "expected_calls"),
    [(0, 1), (2, 3), (9, 3)],
    ids=["first-try", "recovers", "exhausts"],
)
async def test_deliver_retries_then_gives_up(failures: int, expected_calls: int) -> None:
    transport = FlakyTransport(failures=failures)
    client = httpx.AsyncClient(transport=transport)
    dispatcher = WebhookDispatcher(
        FakeSessionFactory(["https://client.example/hook"]), "secret", client
    )
    await dispatcher.status_changed("bot_1", "recording")
    assert len(transport.calls) == expected_calls


async def test_deliver_signs_body() -> None:
    transport = FlakyTransport(failures=0)
    client = httpx.AsyncClient(transport=transport)
    dispatcher = WebhookDispatcher(
        FakeSessionFactory(["https://client.example/hook"]), "secret", client
    )
    await dispatcher.status_changed("bot_1", "joined")
    request = transport.calls[0]
    timestamp = request.headers["X-Webhook-Timestamp"]
    assert request.headers["X-Webhook-Signature"] == sign("secret", request.content, timestamp)


async def test_fanout_skips_without_subscribers() -> None:
    transport = FlakyTransport(failures=0)
    client = httpx.AsyncClient(transport=transport)
    dispatcher = WebhookDispatcher(FakeSessionFactory([]), "secret", client)
    await dispatcher.status_changed("bot_1", "joined")
    assert transport.calls == []
