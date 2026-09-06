import hashlib
import hmac

SIGNATURE_HEADER = "X-Webhook-Signature"


def sign(secret: str, body: bytes, timestamp: str) -> str:
    message = timestamp.encode() + b"." + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
