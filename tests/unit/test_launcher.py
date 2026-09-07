import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from twin.meet.launcher import (
    CLOAK_SEED_FLOOR,
    CLOAK_SEED_SPAN,
    CLOAK_WINDOW_HEIGHT,
    CLOAK_WINDOW_WIDTH,
    CloakBrowser,
    EngineConfig,
    _cloak_args,
    _stable_seed,
)


def _config(tmp_path: Path, guest: bool = True, headed: bool = True) -> EngineConfig:
    return EngineConfig(
        profile_dir=tmp_path / "profile",
        guest=guest,
        headed=headed,
        init_scripts=("hook",),
    )


@pytest.mark.parametrize(
    "stable_id",
    ["tenant-a", "tenant-b", "profiles/guest"],
    ids=["first-tenant", "second-tenant", "guest-tier"],
)
def test_stable_seed_stays_in_fingerprint_range(stable_id: str) -> None:
    seed = _stable_seed(stable_id)
    assert CLOAK_SEED_FLOOR <= seed < CLOAK_SEED_FLOOR + CLOAK_SEED_SPAN


def test_stable_seed_repeats_for_same_identity() -> None:
    assert _stable_seed("tenant-a") == _stable_seed("tenant-a")
    assert _stable_seed("tenant-a") != _stable_seed("tenant-b")


def test_cloak_args_carry_meet_media_and_webrtc_flags(tmp_path: Path) -> None:
    args = _cloak_args(tmp_path / "profile")
    assert "--use-fake-device-for-media-stream" in args
    assert "--force-webrtc-ip-handling-policy=default" in args
    assert "--webrtc-ip-handling-policy=default" in args
    assert f"--window-size={CLOAK_WINDOW_WIDTH},{CLOAK_WINDOW_HEIGHT}" in args


class FakePage:
    pass


class FakeContext:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.granted: list[tuple[list[str], str | None]] = []
        self.closed = False

    async def add_init_script(self, script: str) -> None:
        self.scripts.append(script)

    async def grant_permissions(self, permissions: list[str], origin: str | None = None) -> None:
        self.granted.append((permissions, origin))

    async def new_page(self) -> FakePage:
        return FakePage()

    async def close(self) -> None:
        self.closed = True


def _stub_cloak(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, FakeContext]:
    calls: dict = {}
    context = FakeContext()

    async def launch_persistent_context_async(user_data_dir: str, **kwargs: object) -> FakeContext:
        calls["user_data_dir"] = user_data_dir
        calls["kwargs"] = kwargs
        return context

    stub = SimpleNamespace(launch_persistent_context_async=launch_persistent_context_async)
    monkeypatch.setitem(sys.modules, "cloakbrowser", stub)
    return calls, context


@pytest.mark.parametrize(
    ("guest", "headed", "expect_grant", "expect_headless"),
    [(True, True, True, False), (False, True, False, False), (True, False, True, True)],
    ids=["guest-headed", "signed-headed", "guest-headless"],
)
async def test_cloak_open_wires_profile_and_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guest: bool,
    headed: bool,
    expect_grant: bool,
    expect_headless: bool,
) -> None:
    calls, context = _stub_cloak(monkeypatch)
    profile = tmp_path / "profile"
    profile.mkdir(parents=True)
    (profile / "SingletonLock").write_text("stale")
    config = EngineConfig(
        profile_dir=profile,
        guest=guest,
        headed=headed,
        locale="id-ID",
        timezone="Asia/Jakarta",
        init_scripts=("hook",),
    )
    browser = CloakBrowser(config)
    page = await browser.open("https://meet.google.com/abc-defg-hij")
    assert isinstance(page, FakePage)
    assert calls["user_data_dir"] == str(profile)
    assert calls["kwargs"]["headless"] is expect_headless
    assert calls["kwargs"]["humanize"] is True
    assert calls["kwargs"]["locale"] == "id-ID"
    assert calls["kwargs"]["timezone"] == "Asia/Jakarta"
    assert context.scripts == ["hook"]
    assert bool(context.granted) is expect_grant
    assert not (profile / "SingletonLock").exists()
    await browser.close()
    assert context.closed
