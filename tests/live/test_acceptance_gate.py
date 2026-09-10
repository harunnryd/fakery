import os

import pytest


def test_live_acceptance_fixture_has_three_meetings() -> None:
    value = os.getenv("FAKERY_LIVE_MEETING_URLS")
    if not value:
        pytest.skip("set FAKERY_LIVE_MEETING_URLS for the live English acceptance matrix")
    urls = [item.strip() for item in value.split(",") if item.strip()]
    assert len(urls) >= 3
