from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Any

from app.config import Settings

_TOKEN_PATTERN = re.compile(r"\bbk1\.[0-9a-fA-F-]{36}\.\d+\.[0-9a-f]{64}\b")

_update_context: ContextVar[tuple[int, int] | None] = ContextVar("update_context", default=None)


def build_tracer(settings: Settings):
    if (
        not settings.tracing_enabled
        or not settings.langfuse_public_key
        or not settings.langfuse_secret_key
    ):
        return None
    try:
        # Keep the optional import inside the guard so tracing failures cannot block startup.
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception:
        return None


def get_logger() -> logging.Logger:
    logger = logging.getLogger("service_scheduling")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        level = os.getenv("CAPY_LOG_LEVEL", "INFO").strip().upper()
        logger.setLevel(logging.DEBUG if level == "DEBUG" else logging.INFO)
    return logger


def redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(
        secret in lowered
        for secret in ("token", "phone", "email", "customer_name", "full_name")
    ):
        return "[redacted]" if value else value
    if isinstance(value, str):
        return _TOKEN_PATTERN.sub("[redacted-token]", value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            item_key: redact(item_value, item_key)
            for item_key, item_value in value.items()
        }
    return value


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    safe = redact(fields)
    logger.info({"event": event, **safe})


@contextmanager
def debug_turn(bot_id: int, update_id: int):
    binding = _update_context.set((bot_id, update_id))
    try:
        yield
    finally:
        _update_context.reset(binding)


def debug_event(event: str, **fields: Any) -> None:
    logger = get_logger()
    if not logger.isEnabledFor(logging.DEBUG):
        return
    context = _update_context.get()
    if context is not None:
        bot_id, update_id = context
        fields.setdefault("bot_id", bot_id)
        fields.setdefault("update_id", update_id)
    logger.debug({"level": "debug", "event": event, **redact(fields)})


@contextmanager
def timed_event(event: str, **fields: Any):
    started = monotonic()
    outcome = "ok"
    try:
        yield
    except Exception:
        outcome = "failed"
        raise
    finally:
        log_event(get_logger(), event, outcome=outcome,
                  duration_ms=round((monotonic() - started) * 1000), **fields)


def trace_event(tracer, session_id: str, name: str, payload: dict[str, Any]) -> None:
    if tracer is None:
        return
    try:
        trace = tracer.trace(
            id=session_id, name="service_scheduling", session_id=session_id
        )
        trace.event(name=name, input=redact(payload))
    except Exception:
        return
