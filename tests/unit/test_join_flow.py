from typing import Any

import pytest

from twin.meet import join_flow


async def test_waiting_visible_checks_waiting_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    async def vocabulary(_: Any) -> dict[str, tuple[str, ...]]:
        return {"waiting": ("waiting for someone",)}

    async def text_visible(_: Any, __: str) -> bool:
        return True

    monkeypatch.setattr(join_flow, "_vocabulary", vocabulary)
    monkeypatch.setattr(join_flow, "_text_visible", text_visible)

    assert await join_flow._waiting_visible(object()) is True


@pytest.mark.parametrize(
    ("language", "label"),
    [("id-ID", "Menunggu diizinkan"), ("en-US", "Waiting to be admitted")],
    ids=["indonesian-waiting-room", "english-waiting-room"],
)
async def test_waiting_visible_recognizes_waiting_room(
    monkeypatch: pytest.MonkeyPatch, language: str, label: str
) -> None:
    class Page:
        async def evaluate(self, _: str) -> str:
            return language

    async def text_visible(_: Any, text: str) -> bool:
        return text.casefold() == label.casefold()

    monkeypatch.setattr(join_flow, "_text_visible", text_visible)

    assert await join_flow._waiting_visible(Page()) is True


@pytest.mark.parametrize(
    "body",
    [
        "Your request to join is waiting for host approval",
        "Permintaan Anda menunggu izin untuk bergabung",
        "Asking to be let in. You'll join the call when someone lets you in.",
        "Please wait until a meeting host brings you into the call",
    ],
    ids=[
        "english-variant",
        "indonesian-variant",
        "guest-waiting-screen",
        "host-admission-screen",
    ],
)
async def test_waiting_visible_falls_back_to_body_markers(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    class Body:
        async def inner_text(self) -> str:
            return body

    class Page:
        def locator(self, _: str) -> Body:
            return Body()

    async def vocabulary(_: Any) -> dict[str, tuple[str, ...]]:
        return {"waiting": ()}

    async def text_visible(_: Any, __: str) -> bool:
        return False

    monkeypatch.setattr(join_flow, "_vocabulary", vocabulary)
    monkeypatch.setattr(join_flow, "_text_visible", text_visible)

    assert await join_flow._waiting_visible(Page()) is True
