from types import SimpleNamespace

import pytest

from twin.transcription.decode import PCM_SAMPLE_RATE, ffmpeg_argv
from twin.transcription.transcriber import (
    launch_transcriber,
    live_segment,
    prerecorded_segments,
)


def _word(start: float, end: float, speaker: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, speaker=speaker)


def _live(text: str, words: list, final: bool) -> SimpleNamespace:
    return SimpleNamespace(
        channel=SimpleNamespace(alternatives=[SimpleNamespace(transcript=text, words=words)]),
        start=12.0,
        duration=3.0,
        is_final=final,
        speech_final=False,
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            _live("halo semua", [_word(12.0, 12.4), _word(12.4, 13.1)], True),
            ("halo semua", 12000, 13100, True),
        ),
        (
            _live("sebentar", [], True),
            ("sebentar", 12000, 15000, True),
        ),
        (
            _live("  ", [_word(1.0, 2.0)], True),
            None,
        ),
        (
            _live("draf", [_word(1.0, 2.0)], False),
            ("draf", 1000, 2000, False),
        ),
        (SimpleNamespace(channel=None), None),
    ],
    ids=["words-win", "no-words-fallback", "blank-dropped", "interim-flagged", "no-channel"],
)
def test_live_segment_maps_deepgram_message(message: object, expected: object) -> None:
    segment = live_segment(message)
    if expected is None:
        assert segment is None
    else:
        assert segment is not None
        text, start_ms, end_ms, final = expected  # type: ignore[misc]
        assert (segment.text, segment.start_ms, segment.end_ms, segment.final) == (
            text,
            start_ms,
            end_ms,
            final,
        )


def _sentence(text: str, start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(text=text, start=start, end=end)


def _prerecorded(sentences: list | None, transcript: str) -> SimpleNamespace:
    paragraphs = (
        SimpleNamespace(paragraphs=[SimpleNamespace(sentences=sentences)])
        if sentences is not None
        else None
    )
    return SimpleNamespace(
        results=SimpleNamespace(
            duration=30.0,
            channels=[
                SimpleNamespace(
                    alternatives=[SimpleNamespace(transcript=transcript, paragraphs=paragraphs)]
                )
            ],
        )
    )


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            _prerecorded([_sentence("pagi tim", 1.0, 2.5), _sentence("mulai ya", 3.0, 4.0)], ""),
            [("pagi tim", 1000, 2500), ("mulai ya", 3000, 4000)],
        ),
        (
            _prerecorded(None, "rapat selesai"),
            [("rapat selesai", 0, 30000)],
        ),
        (_prerecorded(None, "   "), []),
        (SimpleNamespace(results=None), []),
    ],
    ids=["sentences", "transcript-fallback", "blank", "no-results"],
)
def test_prerecorded_segments(response: object, expected: list) -> None:
    segments = prerecorded_segments(response)
    assert [(s.text, s.start_ms, s.end_ms) for s in segments] == expected


def _ranged_word(text: str, start: float, end: float, speaker: int | None) -> SimpleNamespace:
    return SimpleNamespace(word=text, start=start, end=end, speaker=speaker)


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (
            [_word(0.0, 1.0, 0), _word(1.0, 2.0, 0), _word(2.0, 3.0, 1)],
            ("halo", 0, 3000, "0"),
        ),
        ([_word(0.0, 1.0, 1), _word(1.0, 2.0, 1)], ("halo", 0, 2000, "1")),
        ([_word(0.0, 1.0, 0), _word(1.0, 2.0, 1)], ("halo", 0, 2000, "0")),
        ([_word(0.0, 1.0, None)], ("halo", 0, 1000, None)),
    ],
    ids=["majority", "unanimous", "tie-earliest", "unlabeled"],
)
def test_live_segment_votes_speaker(words: list, expected: tuple) -> None:
    message = SimpleNamespace(
        channel=SimpleNamespace(alternatives=[SimpleNamespace(transcript="halo", words=words)]),
        start=0.0,
        duration=3.0,
        is_final=True,
        speech_final=False,
    )
    segment = live_segment(message)
    assert segment is not None
    text, start_ms, end_ms, speaker = expected
    assert (segment.text, segment.start_ms, segment.end_ms, segment.speaker) == (
        text,
        start_ms,
        end_ms,
        speaker,
    )


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (
            [_ranged_word("halo", 1.0, 1.5, 1), _ranged_word("tim", 1.5, 2.5, 1)],
            "1",
        ),
        (
            [_ranged_word("halo", 1.0, 2.0, 0), _ranged_word("tim", 2.0, 2.8, 1)],
            "0",
        ),
        ([_ranged_word("jauh", 9.0, 9.5, 1)], None),
        ([_ranged_word("bisu", 1.0, 1.5, None)], None),
    ],
    ids=["unanimous", "tie-earliest", "outside-range", "unlabeled"],
)
def test_prerecorded_assigns_sentence_speaker_from_words(words: list, expected: str | None) -> None:
    sentence = SimpleNamespace(text="halo tim", start=1.0, end=3.0)
    paragraphs = SimpleNamespace(paragraphs=[SimpleNamespace(sentences=[sentence])])
    alternative = SimpleNamespace(transcript="halo tim", paragraphs=paragraphs, words=words)
    response = SimpleNamespace(
        results=SimpleNamespace(channels=[SimpleNamespace(alternatives=[alternative])])
    )
    segments = prerecorded_segments(response)
    assert len(segments) == 1
    assert segments[0].speaker == expected


def test_ffmpeg_argv_decodes_to_mono_pcm() -> None:
    argv = ffmpeg_argv()
    assert argv[:3] == ["ffmpeg", "-hide_banner", "-loglevel"]
    assert argv[-5:] == ["-ac", "1", "-ar", str(PCM_SAMPLE_RATE), "pipe:1"]
    assert "pipe:0" in argv


def test_launch_transcriber_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown transcriber"):
        launch_transcriber("acme", "key")
