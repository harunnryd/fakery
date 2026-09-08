from dataclasses import dataclass, field
from typing import Protocol

from twin.transcription.transcriber import Segment

MAX_TRANSCRIPT_CHARS = 100000

SYSTEM_PROMPT = """You extract meeting notes from a transcript. Rules:
- Summarize what was actually said; never invent decisions, people, or progress.
- Key points are the few claims that mattered.
- Action items need a verb and, only when stated, an owner and a due date.
  Leave owner and due null when unstated — never guess names.
- Reply with JSON only: {"summary": str, "key_points": [str],
  "action_items": [{"text": str, "owner": str | null, "due": str | null}]}."""


@dataclass(slots=True)
class ActionItem:
    text: str
    owner: str | None = None
    due: str | None = None


@dataclass(slots=True)
class MeetingNotes:
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)


class Summarizer(Protocol):
    async def summarize(self, segments: list[Segment]) -> MeetingNotes: ...


def launch_summarizer(kind: str, api_key: str, model: str) -> Summarizer:
    match kind:
        case "openai":
            from twin.notes.openai_notes import OpenAINotes

            return OpenAINotes(api_key, model)
        case _:
            raise ValueError(f"unknown summarizer: {kind}")


def _stamp(start_ms: int) -> str:
    total = start_ms // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def _line(segment: Segment) -> str:
    speaker = f"spk-{segment.speaker}" if segment.speaker is not None else "spk-?"
    return f"[{_stamp(segment.start_ms)}] ({speaker}) {segment.text}"


def notes_prompt(segments: list[Segment]) -> str:
    body = "\n".join(_line(segment) for segment in segments)
    if len(body) > MAX_TRANSCRIPT_CHARS:
        body = body[:MAX_TRANSCRIPT_CHARS] + "\n…[truncated]"
    return f"Transcript:\n{body}"
