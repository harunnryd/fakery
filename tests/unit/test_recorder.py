import base64

from twin.meet.recorder import RECORDER_HOOK, MeetingRecorder


class FakePage:
    def __init__(self) -> None:
        self.frames = [self]
        self.url = "https://meet.google.com/abc-defg-hij"
        self._drained = False

    async def evaluate(self, script: str):
        if "window.__fakeryRecorder" in script:
            return True
        if "data-fakery-chunk" in script:
            if self._drained:
                return []
            self._drained = True
            return [base64.b64encode(b"audio").decode()]
        if "pc_count" in script:
            return {"pc_count": "1", "chunk_bytes": "5"}
        if "window.__fakeryAudioTrack" in script:
            return {"readyState": "live"}
        return None


async def test_recorder_flushes_final_chunk_and_checkpoint() -> None:
    checkpoints: list[tuple[int, bytes]] = []

    async def checkpoint(sequence: int, data: bytes) -> None:
        checkpoints.append((sequence, data))

    recorder = MeetingRecorder(FakePage(), checkpoint_sink=checkpoint)
    await recorder.start()
    recording = await recorder.stop()
    assert recording == b"audio"
    assert checkpoints == [(0, b"audio")]


def test_recorder_hook_uses_stable_mixed_stream() -> None:
    assert "createMediaStreamDestination" in RECORDER_HOOK
    assert "window.__fakeryAudioStream.addTrack" not in RECORDER_HOOK
