from pathlib import Path

import pytest

from twin.bots.runner import _prepare_launch
from twin.core.config import Settings
from twin.meet.recorder import RECORDER_HOOK


@pytest.mark.parametrize(
    ("preset", "expect_guest"),
    [("signed", False), ("guest", True)],
    ids=["signed-tier", "guest-tier"],
)
def test_prepare_launch_arms_recorder_before_page_scripts(
    tmp_path: Path, preset: str, expect_guest: bool
) -> None:
    signed = tmp_path / "signed"
    guest = tmp_path / "guest"
    if preset == "signed":
        signed.mkdir()
    settings = Settings(browser_profile_dir=str(signed), browser_guest_profile_dir=str(guest))
    launch = _prepare_launch(settings)
    assert launch.config.guest is expect_guest
    assert RECORDER_HOOK in launch.config.init_scripts
