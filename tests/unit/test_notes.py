import pytest

from twin.notes.openai_notes import ActionItemSchema, NotesSchema, to_notes
from twin.notes.summarizer import (
    MeetingNotes,
    Segment,
    launch_summarizer,
    notes_prompt,
)
from twin.transcription.transcriber import Segment as TSegment


def _segment(text: str, start_ms: int, speaker: str | None = None) -> TSegment:
    return TSegment(text=text, start_ms=start_ms, end_ms=start_ms + 1000, speaker=speaker)


def test_notes_prompt_numbers_speakers() -> None:
    prompt = notes_prompt(
        [_segment("pagi", 65000, "0"), _segment("halo", 5000, None)],
    )
    assert "[01:05] (spk-0) pagi" in prompt
    assert "[00:05] (spk-?) halo" in prompt


def test_notes_prompt_truncates_long_transcripts() -> None:
    segments = [_segment("kata " * 200, index * 1000) for index in range(500)]
    prompt = notes_prompt(segments)
    assert prompt.endswith("…[truncated]")
    assert len(prompt) < 101000


def test_to_notes_drops_blank_actions_and_owners() -> None:
    schema = NotesSchema(
        summary="rapat",
        key_points=["satu"],
        action_items=[
            ActionItemSchema(text="kirim laporan", owner="Dina", due="jumat"),
            ActionItemSchema(text="  ", owner="X"),
            ActionItemSchema(text="cek", owner="", due=""),
        ],
    )
    notes = to_notes(schema)
    assert isinstance(notes, MeetingNotes)
    assert [(a.text, a.owner, a.due) for a in notes.action_items] == [
        ("kirim laporan", "Dina", "jumat"),
        ("cek", None, None),
    ]


def test_launch_summarizer_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown summarizer"):
        launch_summarizer("acme", "key", "model-x")


def test_segment_alias_matches_provider_shape() -> None:
    assert Segment is TSegment
