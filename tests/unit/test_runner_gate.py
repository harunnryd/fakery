import pytest

from twin.bots.runtime import _capture_gate


class FakePage:
    def __init__(self, shot: bytes | Exception) -> None:
        self._shot = shot

    async def screenshot(self) -> bytes:
        if isinstance(self._shot, Exception):
            raise self._shot
        return self._shot


class FakeBlob:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.puts.append((key, data, content_type))
        return f"s3://bucket/{key}"


@pytest.mark.parametrize(
    ("shot", "blob", "expect_puts"),
    [
        (b"png-bytes", True, 1),
        (b"", True, 0),
        (RuntimeError("no page"), True, 0),
        (b"png-bytes", False, 0),
    ],
    ids=["uploads-shot", "skips-empty", "tolerates-shot-error", "tolerates-no-blob"],
)
async def test_capture_gate_persists_shot_only_when_useful(
    shot: bytes | Exception, blob: bool, expect_puts: int
) -> None:
    store = FakeBlob() if blob else None
    await _capture_gate(FakePage(shot), "bot_x", store)
    puts = store.puts if store else []
    assert len(puts) == expect_puts
    if expect_puts:
        key, data, content_type = puts[0]
        assert key == "gates/bot_x.png"
        assert data == b"png-bytes"
        assert content_type == "image/png"
