"""Outbound webhook signature.

HMAC-SHA256 over f"{timestamp}.{body}" under the shared signing
secret, sent as X-Webhook-Signature: sha256=<hex>; clients recompute
it over the raw body they receive. Envelope + delivery with retries
land with the webhook module (PLAN module 9).
"""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Webhook-Signature"


def sign(secret: str, body: bytes, timestamp: str) -> str:
    message = timestamp.encode() + b"." + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
