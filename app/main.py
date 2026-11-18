"""Health checks and durable Telegram webhook intake."""

import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from redis import Redis

from app.channels.bots import BotRegistry
from app.channels.telegram_intake import save_update
from app.config import Settings, load_settings
from app.database import DATABASE_ERRORS, database_is_ready
from app.models import HealthResponse
from app.observability import debug_event, get_logger, log_event

WEBHOOK_BODY_LIMIT = 256 * 1024


class _SuppressHealthAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if (
            isinstance(record.args, tuple)
            and len(record.args) >= 3
            and str(record.args[2]).split("?", 1)[0] == "/health"
        ):
            return False
        return '"GET /health ' not in record.getMessage()


def _silence_health_access_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _SuppressHealthAccess) for item in logger.filters):
        logger.addFilter(_SuppressHealthAccess())


_silence_health_access_logs()


@dataclass
class HealthDependencies:
    settings: Settings
    redis: Redis


@asynccontextmanager
async def lifespan(app):
    settings = load_settings()
    redis_client = Redis.from_url(
        settings.chat_redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    app.state.health_dependencies = HealthDependencies(settings, redis_client)
    debug_event("api.started")
    try:
        yield
    finally:
        redis_client.close()


app = FastAPI(title="CAPY scheduling", lifespan=lifespan)


def get_health_dependencies(request: Request) -> HealthDependencies:
    return request.app.state.health_dependencies


@app.get("/live")
def live():
    return {"status": "ok"}


@app.post("/telegram/{route}", status_code=204)
async def telegram_webhook(
    route: str, request: Request, dependencies: HealthDependencies = Depends(get_health_dependencies)
):
    started = time.monotonic()
    database_url = dependencies.settings.database_url
    try:
        bot = await run_in_threadpool(BotRegistry(database_url).by_route, route)
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if bot is None or not bot.accepts_webhook(secret):
            debug_event("telegram.webhook.rejected", reason="authentication")
            raise HTTPException(status_code=403, detail="Webhook rejected")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > WEBHOOK_BODY_LIMIT:
                debug_event("telegram.webhook.rejected", reason="body_too_large")
                raise HTTPException(status_code=413, detail="Update too large")
            body.extend(chunk)
        try:
            update = json.loads(body)
        except (ValueError, RecursionError) as exc:
            debug_event("telegram.webhook.rejected", reason="invalid_json")
            raise HTTPException(status_code=400, detail="Invalid Telegram update") from exc
        if not isinstance(update, dict) or not await run_in_threadpool(
            save_update, database_url, bot, update
        ):
            raise HTTPException(status_code=400, detail="Invalid Telegram update")
    except DATABASE_ERRORS as exc:
        debug_event("telegram.webhook.rejected", reason="database_unavailable")
        raise HTTPException(status_code=503, detail="Intake unavailable") from exc
    log_event(get_logger(), "telegram.webhook.accepted",
              duration_ms=round((time.monotonic() - started) * 1000))
    return Response(status_code=204)


@app.get("/health", response_model=HealthResponse)
def health(dependencies: HealthDependencies = Depends(get_health_dependencies)):
    database_ok = database_is_ready(dependencies.settings.database_url)
    try:
        redis_ok = bool(dependencies.redis.ping())
    except Exception:
        redis_ok = False
    response = HealthResponse(
        status="ok" if database_ok and redis_ok else "degraded",
        database="ok" if database_ok else "unavailable",
        chat_history="ok" if redis_ok else "unavailable",
    )
    if not database_ok or not redis_ok:
        debug_event("api.health.degraded", database_ok=database_ok, redis_ok=redis_ok)
    return (
        response
        if response.status == "ok"
        else JSONResponse(status_code=503, content=response.model_dump())
    )
