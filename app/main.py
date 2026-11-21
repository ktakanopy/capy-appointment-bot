"""Health checks and durable Telegram webhook intake."""

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis import Redis

from app.agenda_api import router as agenda_router
from app.channels.bots import configured_bot
from app.channels.telegram_intake import save_update
from app.channels.telegram_queue import TelegramQueue
from app.config import Settings, load_settings
from app.database import DATABASE_ERRORS, database_is_ready
from app.models import DomainError, HealthResponse
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
    queue: TelegramQueue | None = None


@asynccontextmanager
async def lifespan(app):
    settings = load_settings()
    redis_client = Redis.from_url(
        settings.chat_redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    app.state.settings = settings
    bot = configured_bot(settings)
    queue = TelegramQueue(settings.rabbitmq_url.get_secret_value(), bot.bot_id, get_logger()) if bot else None
    if queue:
        queue.start()
    app.state.health_dependencies = HealthDependencies(settings, redis_client, queue)
    debug_event("api.started")
    try:
        yield
    finally:
        if queue:
            await queue.close()
        redis_client.close()


app = FastAPI(title="CAPY scheduling", lifespan=lifespan)
app.include_router(agenda_router)


@app.exception_handler(DomainError)
async def domain_error(_request: Request, error: DomainError):
    return JSONResponse(
        status_code=error.status_code, content={"code": error.code, "detail": error.message}
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "Invalid request fields"})



def get_health_dependencies(request: Request) -> HealthDependencies:
    return request.app.state.health_dependencies


@app.get("/live")
def live():
    return {"status": "ok"}


@app.post("/telegram/{route}", status_code=204, max_body_size=WEBHOOK_BODY_LIMIT)
async def telegram_webhook(
    route: str,
    request: Request,
    dependencies: HealthDependencies = Depends(get_health_dependencies),
):
    """Authenticate and durably accept one Telegram update for its configured bot.

    The route identifies the bot and its business. After checking Telegram's
    webhook secret, the handler validates a size-limited JSON payload and saves
    the update to the inbox. Returning HTTP 204 only after that save lets
    Telegram retry when intake fails instead of losing an update.
    """
    started = time.monotonic()
    database_url = dependencies.settings.database_url
    try:
        bot = configured_bot(dependencies.settings)
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if (
            bot is None
            or route != dependencies.settings.telegram_webhook_route
            or not bot.accepts_webhook(secret)
        ):
            debug_event("telegram.webhook.rejected", reason="authentication")
            raise HTTPException(status_code=403, detail="Webhook rejected")

        try:
            update = await request.json()
        except (ValueError, RecursionError) as exc:
            debug_event("telegram.webhook.rejected", reason="invalid_json")
            raise HTTPException(
                status_code=400, detail="Invalid Telegram update"
            ) from exc

        # Save before acknowledging so Telegram can retry if durable intake fails.
        if not isinstance(update, dict):
            raise HTTPException(status_code=400, detail="Invalid Telegram update")
        result = await run_in_threadpool(save_update, database_url, bot, update)
        if not result.accepted:
            raise HTTPException(status_code=400, detail="Invalid Telegram update")
    except DATABASE_ERRORS as exc:
        debug_event("telegram.webhook.rejected", reason="database_unavailable")
        raise HTTPException(status_code=503, detail="Intake unavailable") from exc
    if result.notification is not None and dependencies.queue is not None:
        await dependencies.queue.try_publish(
            result.notification, timeout=dependencies.settings.telegram_notification_timeout_seconds,
        )
    log_event(
        get_logger(),
        "telegram.webhook.accepted",
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return Response(status_code=204)


@app.get("/health", response_model=HealthResponse)
def health(dependencies: HealthDependencies = Depends(get_health_dependencies)):
    database_ok = database_is_ready(dependencies.settings.database_url)
    try:
        redis_ok = bool(dependencies.redis.ping())
    except Exception:
        redis_ok = False
    queue = getattr(dependencies, "queue", None)
    broker = "ok" if queue and queue.connected else "unavailable" if queue else "disabled"
    response = HealthResponse(
        status="ok" if database_ok and redis_ok and broker != "unavailable" else "degraded",
        database="ok" if database_ok else "unavailable",
        chat_history="ok" if redis_ok else "unavailable",
        broker=broker,
    )
    if not database_ok or not redis_ok:
        debug_event("api.health.degraded", database_ok=database_ok, redis_ok=redis_ok)
    return (
        response
        if database_ok and redis_ok
        else JSONResponse(status_code=503, content=response.model_dump())
    )
