"""Health surface. Customer operations enter exclusively through Telegram."""
import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from app.runtime import create_runtime, close_runtime
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



@asynccontextmanager
async def lifespan(app):
    app.state.runtime = create_runtime()
    try:
        yield
    finally:
        close_runtime(app.state.runtime)

app = FastAPI(title="CAPY scheduling health", lifespan=lifespan)

def get_runtime(request: Request):
    return request.app.state.runtime

@app.get("/health", response_model=HealthResponse)
def health(runtime=Depends(get_runtime)):
    database_ok = database_is_ready(runtime.settings.database_url)
    try:
        redis_ok = bool(runtime.redis.ping())
    except Exception:
        redis_ok = False
    response = HealthResponse(status="ok" if database_ok and redis_ok else "degraded",
                          database="ok" if database_ok else "unavailable",
                          chat_history="ok" if redis_ok else "unavailable")
    return response if response.status == "ok" else JSONResponse(status_code=503, content=response.model_dump())
