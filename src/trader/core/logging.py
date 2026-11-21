"""JSON logging: one object per line with ts, level, service, msg."""

import json
import logging
from datetime import UTC
from typing import Any

from trader.core.clock import Clock, SystemClock

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class _JsonFormatter(logging.Formatter):
    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self._clock = clock

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self._clock.now().astimezone(UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "trader"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class _ServiceFilter(logging.Filter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "service"):
            record.service = self._service
        return True


def setup(
    service: str = "trader", level: str = "INFO", clock: Clock | None = None
) -> None:
    """Configure the root and uvicorn loggers to emit one JSON object per line."""
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter(clock or SystemClock()))
    handler.addFilter(_ServiceFilter(service))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(level.upper())
