from datetime import UTC, datetime

SECONDS_PER_MINUTE = 60


def utcnow() -> datetime:
    return datetime.now(UTC)
