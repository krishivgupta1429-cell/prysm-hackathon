"""
app/core/logging.py
Structured JSON logging with correlation IDs, request context,
and per-module log levels.
"""

import json
import logging
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.config import settings

# ── Context vars (set per request in middleware) ──────────────────────────────
request_id_var:  ContextVar[str]  = ContextVar("request_id",  default="")
user_id_var:     ContextVar[str]  = ContextVar("user_id",     default="")
endpoint_var:    ContextVar[str]  = ContextVar("endpoint",    default="")


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    LEVEL_MAP = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO",
        logging.WARNING:  "WARNING",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level":     self.LEVEL_MAP.get(record.levelno, "UNKNOWN"),
            "logger":    record.name,
            "message":   record.getMessage(),
            "service":   "creator-ai",
            "env":       settings.APP_ENV,
        }

        # Correlation context
        rid = request_id_var.get("")
        uid = user_id_var.get("")
        ep  = endpoint_var.get("")
        if rid: payload["request_id"] = rid
        if uid: payload["user_id"]    = uid
        if ep:  payload["endpoint"]   = ep

        # Extra fields attached via logger.info("msg", extra={...})
        for key, val in record.__dict__.items():
            if key.startswith("x_") or key in ("job_id", "media_id", "task", "duration_ms"):
                payload[key] = val

        # Exception
        if record.exc_info:
            payload["exception"] = {
                "type":    record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]),
                "trace":   traceback.format_exception(*record.exc_info),
            }

        # Source location (only in debug)
        if settings.DEBUG:
            payload["source"] = f"{record.pathname}:{record.lineno}"

        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Wire up the root logger with JSON output to stdout."""
    level = logging.DEBUG if settings.DEBUG else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    # Remove existing handlers (uvicorn adds its own)
    root.handlers.clear()
    root.addHandler(handler)

    # Quieten noisy libraries
    for noisy in ("uvicorn.access", "httpx", "sqlalchemy.engine", "botocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def new_request_id() -> str:
    return str(uuid.uuid4())[:8]
