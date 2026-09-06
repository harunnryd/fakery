import logging
import sys

import structlog

_REDACT_KEYS = (
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "authorization",
    "cookie",
    "set-cookie",
    "meeting_url",
)


def _redact(_, __, event_dict: dict) -> dict:
    for key in list(event_dict):
        lowered = key.lower()
        if any(denied in lowered for denied in _REDACT_KEYS):
            value = event_dict[key]
            length = len(str(value)) if value else 0
            event_dict[key] = f"<redacted:{length}>"
    return event_dict


_configured = False


def configure_logging(json_logs: bool, log_level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    _configured = True
