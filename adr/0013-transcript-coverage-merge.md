# Transcript coverage merge

Status: accepted

Live STT rows are persisted while a meeting is active so receivers do not
wait for finalization. Batch transcription still runs after capture because a
live stream can disconnect or miss audio. Batch rows that are at least a
quarter covered by the existing live timeline are discarded during
finalization; only uncovered rows are added. Speaker annotations use the
complete batch result so the live rows retain diarization without creating
duplicate utterances.

This keeps one visible row per covered time range and prevents finalization
from replaying the entire recording into the transcript. A later revision
event can replace a live row when batch text is materially better; until that
event exists, coverage wins over silent replacement.
