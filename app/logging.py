"""JSON logging to stdout, one object per line.

Write logs with `log("ticket.classified", ticket_id=...)`. Never log ticket bodies, model output or keys.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# Set by the API for each request, and added to every log line written while handling it.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_logger = logging.getLogger("app")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if request_id.get() is not None:
            entry["request_id"] = request_id.get()
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(level: str) -> None:
    """Send every log line to stdout as JSON, including uvicorn's."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def log(event: str, level: int = logging.INFO, **fields: object) -> None:
    _logger.log(level, event, extra={"fields": fields})
