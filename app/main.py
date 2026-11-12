"""Health surface. Customer operations enter exclusively through Telegram."""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from redis import Redis

from app.config import Settings, load_settings
from app.database import database_is_ready
from app.models import HealthResponse


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
    try:
        yield
    finally:
        redis_client.close()


app = FastAPI(title="CAPY scheduling health", lifespan=lifespan)


def get_health_dependencies(request: Request) -> HealthDependencies:
    return request.app.state.health_dependencies


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
    return (
        response
        if response.status == "ok"
        else JSONResponse(status_code=503, content=response.model_dump())
    )
