from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

MEDIA_ARGS = (
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
)

CLOAK_WINDOW_WIDTH = 1280
CLOAK_WINDOW_HEIGHT = 720
CLOAK_SEED_FLOOR = 10_000
CLOAK_SEED_SPAN = 90_000
CLOAK_SINGLETON_LOCKS = ("SingletonLock", "SingletonCookie", "SingletonSocket")
CLOAK_MEET_ORIGIN = "https://meet.google.com"


class MeetBrowser(Protocol):
    async def open(self, meeting_url: str) -> Any: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class EngineConfig:
    profile_dir: Path
    headed: bool = True
    locale: str = "id-ID"
    timezone: str = "Asia/Jakarta"
    guest: bool = False
    init_scripts: tuple[str, ...] = field(default_factory=tuple)
    fake_audio_path: Path | None = None


def _stable_seed(stable_id: str) -> int:
    digest = sha256(stable_id.encode()).digest()
    return CLOAK_SEED_FLOOR + (int.from_bytes(digest[:4], "big") % CLOAK_SEED_SPAN)


def _clean_stale_locks(profile_dir: Path) -> None:
    for name in CLOAK_SINGLETON_LOCKS:
        (profile_dir / name).unlink(missing_ok=True)


def _cloak_args(profile_dir: Path, fake_audio_path: Path | None = None) -> list[str]:
    seed = _stable_seed(str(profile_dir))
    args = [
        f"--fingerprint={seed}",
        f"--fingerprint-screen-width={CLOAK_WINDOW_WIDTH}",
        f"--fingerprint-screen-height={CLOAK_WINDOW_HEIGHT}",
        f"--window-size={CLOAK_WINDOW_WIDTH},{CLOAK_WINDOW_HEIGHT}",
        "--window-position=0,0",
        *MEDIA_ARGS,
        "--disable-blink-features=AutomationControlled",
        "--disable-notifications",
        "--disable-extensions",
        "--disable-crash-reporter",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--test-type",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--use-mock-keychain",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-features=PasswordManagerOnboarding,AutofillEnableAccountWalletStorage,PasswordImport,PasswordsAccountStorage,Translate",
        "--force-webrtc-ip-handling-policy=default",
        "--webrtc-ip-handling-policy=default",
    ]
    if fake_audio_path is not None:
        args.extend(
            (
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={fake_audio_path}",
            )
        )
    return args


class CloakBrowser:
    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._context: Any = None

    async def open(self, meeting_url: str) -> Any:
        from cloakbrowser import launch_persistent_context_async

        config = self._config
        profile_dir = config.profile_dir.expanduser()
        profile_dir.mkdir(parents=True, exist_ok=True)
        _clean_stale_locks(profile_dir)
        self._context = await launch_persistent_context_async(
            str(profile_dir),
            headless=not config.headed,
            locale=config.locale,
            timezone=config.timezone,
            viewport=None,
            humanize=True,
            args=_cloak_args(profile_dir, config.fake_audio_path),
        )
        for script in config.init_scripts:
            await self._context.add_init_script(script)
        if config.guest:
            await self._context.grant_permissions(
                ["microphone", "camera"], origin=CLOAK_MEET_ORIGIN
            )
        return await self._context.new_page()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
