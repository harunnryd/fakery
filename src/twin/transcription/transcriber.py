from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class Segment:
    text: str
    start_ms: int
    end_ms: int
    final: bool = True
    speaker: str | None = None


SegmentSink = Callable[[Segment], Awaitable[None]]


class Transcriber(Protocol):
    async def transcribe_stream(self, audio: AsyncIterator[bytes], sink: SegmentSink) -> int: ...

    async def transcribe_recording(self, audio: bytes) -> list[Segment]: ...


def launch_transcriber(
    kind: str, api_key: str, diarize: bool = True, language: str = "en"
) -> Transcriber:
    match kind:
        case "deepgram":
            from twin.transcription.deepgram import DeepgramTranscriber

            return DeepgramTranscriber(api_key, diarize, language)
        case _:
            raise ValueError(f"unknown transcriber: {kind}")


def _words_of(message: Any) -> list:
    try:
        return list(message.channel.alternatives[0].words or [])
    except (AttributeError, IndexError, TypeError):
        return []


def _majority_speaker(words: list) -> str | None:
    counts: dict[int, int] = {}
    order: list[int] = []
    for word in words:
        speaker = getattr(word, "speaker", None)
        if speaker is None:
            continue
        if speaker not in counts:
            counts[speaker] = 0
            order.append(speaker)
        counts[speaker] += 1
    if not counts:
        return None
    return str(max(order, key=lambda speaker: counts[speaker]))


def live_segment(message: Any) -> Segment | None:
    try:
        alternative = message.channel.alternatives[0]
        text = (alternative.transcript or "").strip()
    except (AttributeError, IndexError, TypeError):
        return None
    if not text:
        return None
    words = _words_of(message)
    if words:
        start_ms = int(words[0].start * 1000)
        end_ms = int(words[-1].end * 1000)
    else:
        start_ms = int(message.start * 1000)
        end_ms = int((message.start + message.duration) * 1000)
    final = bool(getattr(message, "is_final", False) or getattr(message, "speech_final", False))
    return Segment(
        text=text, start_ms=start_ms, end_ms=end_ms, final=final, speaker=_majority_speaker(words)
    )


def prerecorded_segments(response: Any) -> list[Segment]:
    try:
        alternative = response.results.channels[0].alternatives[0]
    except (AttributeError, IndexError, TypeError):
        return []
    sentences: list = []
    for paragraph in getattr(getattr(alternative, "paragraphs", None), "paragraphs", None) or []:
        sentences.extend(getattr(paragraph, "sentences", None) or [])
    words = list(getattr(alternative, "words", None) or [])
    segments = [
        Segment(
            text=sentence.text.strip(),
            start_ms=int(sentence.start * 1000),
            end_ms=int(sentence.end * 1000),
            speaker=_batch_speaker(sentence, words),
        )
        for sentence in sentences
        if (getattr(sentence, "text", "") or "").strip()
    ]
    if segments:
        return segments
    text = (getattr(alternative, "transcript", "") or "").strip()
    if not text:
        return []
    duration = getattr(response.results, "duration", 0) or 0
    return [Segment(text=text, start_ms=0, end_ms=int(duration * 1000))]


def _batch_speaker(sentence: Any, words: list) -> str | None:
    direct = getattr(sentence, "speaker", None)
    if direct is not None:
        return str(direct)
    return _majority_speaker(
        _words_in_range(words, int(sentence.start * 1000), int(sentence.end * 1000))
    )


def _words_in_range(words: list, start_ms: int, end_ms: int) -> list:
    return [
        word
        for word in words
        if int(word.start * 1000) < end_ms and int(word.end * 1000) > start_ms
    ]
