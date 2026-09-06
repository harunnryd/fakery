import pytest

from twin.core.errors import _CATALOG, TwinError, make_error
from twin.core.logging import _redact


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("api_key", "abcd1234", "<redacted:8>"),
        ("webhook_signing_secret", "hush", "<redacted:4>"),
        ("authorization", "Bearer xyz", "<redacted:10>"),
        ("set-cookie", "", "<redacted:0>"),
        ("meeting_url", "https://meet.google.com/abc-defg-hij", "<redacted:36>"),
    ],
    ids=["api_key", "secret_suffix", "authorization", "empty_value", "meeting_url"],
)
def test_redact_masks_sensitive_values_by_length_only(key: str, value: str, expected: str) -> None:
    assert _redact(None, None, {key: value})[key] == expected


def test_redact_leaves_innocent_keys_untouched() -> None:
    event = {"bot_id": "bot_abc", "status": "queued"}
    assert _redact(None, None, event) == event


@pytest.mark.parametrize(
    ("slug", "expected_status"),
    [(slug, spec[1]) for slug, spec in _CATALOG.items()],
    ids=list(_CATALOG.keys()),
)
def test_make_error_maps_slug_to_catalog_status(slug: str, expected_status: int) -> None:
    error = make_error(slug, detail="d")
    assert error.status == expected_status
    assert error.slug == slug
    assert error.type_url().endswith(f"/{slug}")


def test_make_error_unknown_slug_is_a_programmer_error() -> None:
    with pytest.raises(ValueError, match="unknown error slug"):
        make_error("totally-made-up")


def test_twin_error_problem_envelope_carries_request_id() -> None:
    error = TwinError(slug="not-found", title="Resource not found", status=404, detail="x")
    problem = error.to_problem(request_id="req_1")
    assert problem["instance"] == "req_1"
    assert problem["status"] == 404
