from datetime import UTC, datetime

import pytest

from twin.bots.models import BotRun, TranscriptSegment
from twin.bots.service import BotService
from twin.bots.state import BotStatus
from twin.transcription.transcriber import Segment


class FakeEvents:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []
        self.segments: list[TranscriptSegment] = []

    async def status_changed(self, bot_id: str, status: str) -> None:
        self.statuses.append((bot_id, status))

    async def transcript_segment(self, segment: TranscriptSegment) -> None:
        self.segments.append(segment)


class FakeRepo:
    def __init__(self) -> None:
        self.runs: dict[str, BotRun] = {}
        self.saved: list[TranscriptSegment] = []

    async def add(self, run: BotRun) -> None:
        self.runs[run.id] = run

    async def get(self, bot_id: str) -> BotRun | None:
        return self.runs.get(bot_id)

    async def segments(self, bot_id: str) -> list:
        return [seg for seg in self.saved if seg.bot_run_id == bot_id]

    async def add_segment(self, segment: TranscriptSegment) -> None:
        self.saved.append(segment)

    async def annotate_speakers(self, bot_id: str, start_ms: int, end_ms: int, speaker: str) -> int:
        count = 0
        for segment in self.saved:
            if (
                segment.bot_run_id == bot_id
                and segment.speaker is None
                and segment.start_ms < end_ms
                and segment.end_ms > start_ms
            ):
                segment.speaker = speaker
                count += 1
        return count


def _service(events: FakeEvents | None = None) -> tuple[BotService, FakeRepo]:
    repo = FakeRepo()
    fixed = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    service = BotService(repo, clock=lambda: fixed, events=events)
    return service, repo


async def test_advance_emits_every_transition() -> None:
    events = FakeEvents()
    service, _ = _service(events)
    run = await service.create("https://meet.google.com/abc-defg-hij")
    await service.advance(run.id, BotStatus.JOINING)
    await service.advance(run.id, BotStatus.FAILED)
    assert events.statuses == [(run.id, "joining"), (run.id, "failed")]


async def test_advance_silent_without_sink() -> None:
    service, _ = _service(None)
    run = await service.create("https://meet.google.com/abc-defg-hij")
    await service.advance(run.id, BotStatus.JOINING)
    assert (await service.get(run.id)).status == "joining"


async def test_ingest_persists_and_emits_segment() -> None:
    events = FakeEvents()
    service, repo = _service(events)
    run = await service.create("https://meet.google.com/abc-defg-hij")
    segment = await service.ingest_segment(
        run.id, Segment(text="setuju", start_ms=1000, end_ms=2000)
    )
    assert segment.id.startswith("seg_")
    assert [seg.text for seg in await service.transcript(run.id)] == ["setuju"]
    assert events.segments == [segment]
    assert repo.saved == [segment]


@pytest.mark.parametrize("speaker", ["Dina", None], ids=["named", "anonymous"])
async def test_ingest_keeps_speaker(speaker: str | None) -> None:
    service, _ = _service(None)
    run = await service.create("https://meet.google.com/abc-defg-hij")
    segment = await service.ingest_segment(
        run.id, Segment(text="ok", start_ms=0, end_ms=500, speaker=speaker)
    )
    assert segment.speaker == speaker


async def test_annotate_fills_only_blank_overlapping_segments() -> None:
    service, _ = _service(None)
    run = await service.create("https://meet.google.com/abc-defg-hij")
    await service.ingest_segment(run.id, Segment(text="pagi", start_ms=1000, end_ms=2000))
    await service.ingest_segment(run.id, Segment(text="siang", start_ms=5000, end_ms=6000))
    await service.ingest_segment(
        run.id, Segment(text="sore", start_ms=1500, end_ms=2500, speaker="9")
    )
    annotated = await service.annotate_speakers(
        run.id, [Segment(text="x", start_ms=500, end_ms=2500, speaker="1")]
    )
    assert annotated == 1
    texts = {seg.text: seg.speaker for seg in await service.transcript(run.id)}
    assert texts == {"pagi": "1", "siang": None, "sore": "9"}


async def test_annotate_skips_speakerless_batch() -> None:
    service, _ = _service(None)
    run = await service.create("https://meet.google.com/abc-defg-hij")
    await service.ingest_segment(run.id, Segment(text="pagi", start_ms=1000, end_ms=2000))
    annotated = await service.annotate_speakers(
        run.id, [Segment(text="x", start_ms=0, end_ms=3000, speaker=None)]
    )
    assert annotated == 0
