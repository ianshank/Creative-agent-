"""Structured logging and debug instrumentation.

A review makes many LLM calls, fetches sources, caps severities, and writes state; when
a verdict looks wrong the question is always *which stage decided that*. Every stage
therefore logs a structured record with a stable `event` name, so logs can be grepped or
parsed rather than read.

Two output modes, both configured (never hard-coded at a call site):
- `text` (default): human-readable, one line per event.
- `json`: one JSON object per line, for shipping to a log store.

Log records carry contextual fields via the `extra={"context": {...}}` convention; the
formatters render them. Nothing here writes secrets: prompts and artifact text are never
logged, only their sizes and identifiers.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

LOGGER_NAMESPACE = "creative_agent"
_CONTEXT_KEY = "context"
# Reserved LogRecord attributes, used to separate caller-supplied fields from stdlib ones.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def get_logger(name: str) -> logging.Logger:
    """Return the namespaced logger for a module (`creative_agent.harness.pipeline`)."""
    if name.startswith(f"{LOGGER_NAMESPACE}.") or name == LOGGER_NAMESPACE:
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{name}")


def _record_context(record: logging.LogRecord) -> dict[str, Any]:
    context = getattr(record, _CONTEXT_KEY, None)
    if isinstance(context, Mapping):
        return dict(context)
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED}


class TextContextFormatter(logging.Formatter):
    """`LEVEL logger: message [k=v k=v]` — readable, still greppable."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context = _record_context(record)
        # The event name is already the message; repeating it as a field is noise.
        if context.get("event") == record.getMessage():
            context.pop("event")
        if not context:
            return base
        rendered = " ".join(f"{key}={value!r}" for key, value in sorted(context.items()))
        return f"{base} [{rendered}]"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for machine consumption."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_record_context(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(
    level: str = "WARNING",
    log_format: str = "text",
    *,
    stream: Any = None,
) -> logging.Logger:
    """Configure the package logger. Idempotent: replaces its own handler on re-entry.

    Only the `creative_agent` namespace is touched — importing this library never
    reconfigures the root logger of a host application.
    """
    logger = logging.getLogger(LOGGER_NAMESPACE)
    resolved = logging.getLevelName(level.strip().upper())
    if not isinstance(resolved, int):
        raise ValueError(f"unknown log level: {level!r}")
    logger.setLevel(resolved)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setLevel(resolved)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    elif log_format == "text":
        handler.setFormatter(TextContextFormatter("%(levelname)s %(name)s: %(message)s"))
    else:
        raise ValueError(f"unknown log format: {log_format!r} (expected 'text' or 'json')")
    logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger, level: int, event: str, /, **context: Any
) -> None:
    """Emit one structured event. `event` is a stable, greppable identifier."""
    logger.log(level, event, extra={_CONTEXT_KEY: {"event": event, **context}})


@contextmanager
def timed_stage(logger: logging.Logger, stage: str, /, **context: Any) -> Iterator[dict[str, Any]]:
    """Debug-log a stage's start/end with its duration and any fields it records.

    The yielded dict is mutable: a stage can add outcome fields (counts, verdicts) that
    are included in the completion record.
    """
    extra: dict[str, Any] = {}
    started = time.perf_counter()
    log_event(logger, logging.DEBUG, f"{stage}.start", **context)
    try:
        yield extra
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            f"{stage}.failed",
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            error_type=type(exc).__name__,
            **context,
            **extra,
        )
        raise
    log_event(
        logger,
        logging.DEBUG,
        f"{stage}.done",
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
        **context,
        **extra,
    )
