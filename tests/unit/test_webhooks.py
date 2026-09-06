import hashlib
import hmac

from twin.webhooks.signing import sign

SECRET = "test-secret"


def _expected_digest(timestamp: str, body: bytes) -> str:
    message = timestamp.encode() + b"." + body
    return hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()


def test_sign_is_deterministic_for_identical_input() -> None:
    first = sign(SECRET, b"{}", "123")
    second = sign(SECRET, b"{}", "123")
    assert first == second == f"sha256={_expected_digest('123', b'{}')}"


def test_sign_changes_when_body_changes() -> None:
    assert sign(SECRET, b"a", "123") != sign(SECRET, b"b", "123")
