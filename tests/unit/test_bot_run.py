import pytest

from twin.bot_run import _error_location, _error_slug


def test_error_location_reports_last_frame_without_exception_text() -> None:
    try:
        raise TypeError("sensitive detail")
    except TypeError as err:
        location = _error_location(err)

    assert location["error_type"] == "TypeError"
    assert location["error_function"] == (
        "test_error_location_reports_last_frame_without_exception_text"
    )
    assert "sensitive detail" not in str(location)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("wait"), "join-timeout"),
        (RuntimeError("target page has been closed"), "join-closed"),
        (OSError("network down"), "join-connection"),
        (ValueError("unexpected"), "join-error"),
    ],
    ids=["timeout", "closed", "connection", "unknown"],
)
def test_error_slug_omits_exception_details(error: Exception, expected: str) -> None:
    assert _error_slug(error, "join") == expected
    assert str(error) not in _error_slug(error, "join")
