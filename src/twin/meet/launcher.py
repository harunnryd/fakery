from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

FAKE_MEDIA_ARGS = (
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
)


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


class PatchrightBrowser:
    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._pw: Any = None
        self._context: Any = None

    async def open(self, meeting_url: str) -> Any:
        from patchright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._context = await self._new_context()
        for script in self._config.init_scripts:
            await self._context.add_init_script(script)
        return await self._context.new_page()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._pw is not None:
            await self._pw.stop()

    async def _new_context(self) -> Any:
        config = self._config
        if config.guest:
            browser = await self._pw.chromium.launch(
                headless=not config.headed, args=FAKE_MEDIA_ARGS
            )
            context = await browser.new_context(
                locale=config.locale, timezone_id=config.timezone, viewport=None
            )
            await context.grant_permissions(
                ["microphone", "camera"], origin="https://meet.google.com"
            )
            return context
        profile_dir = config.profile_dir.expanduser()
        profile_dir.mkdir(parents=True, exist_ok=True)
        return await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=not config.headed,
            args=FAKE_MEDIA_ARGS,
            no_viewport=True,
            locale=config.locale,
            timezone_id=config.timezone,
        )
