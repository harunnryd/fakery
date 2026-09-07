import pytest

from twin.bots.runtime import _confident_split
from twin.transcription.transcriber import Segment


def _segment(speaker: str | None, start_ms: int, end_ms: int) -> Segment:
    return Segment(text="x", start_ms=start_ms, end_ms=end_ms, speaker=speaker)


@pytest.mark.parametrize(
    ("speakers", "expected"),
    [
        ([("0", 0, 60000), ("1", 60000, 120000)], True),
        ([("0", 0, 85000), ("1", 85000, 100000)], True),
        ([("0", 0, 86000), ("1", 86000, 100000)], False),
        ([("0", 0, 60000)], False),
        ([(None, 0, 60000), (None, 60000, 120000)], False),
        ([], False),
    ],
    ids=[
        "balanced",
        "boundary-share",
        "collapsed",
        "single-voice",
        "unlabeled",
        "empty",
    ],
)
def test_confident_split_gates_uncertain_diarization(speakers: list, expected: bool) -> None:
    segments = [_segment(speaker, start, end) for speaker, start, end in speakers]
    assert _confident_split(segments) is expected
