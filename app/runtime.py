from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from redis import Redis

from app.booking import BookingService
from app.channels.audio_chunks import AudioChunker
from app.config import Settings, load_settings
from app.graph.builder import build_graph
from app.graph.redis_checkpoint import RedisCheckpointSaver
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import GemmaUnderstanding
from app.llm.provider import build_decision, build_interpreter
from app.observability import build_tracer, debug_event, get_logger
from app.repositories import PostgresRepository
from app.scheduling import AvailabilityService


@dataclass
class RuntimeContext:
    settings: Settings
    logger: object
    tracer: object | None
    repository: PostgresRepository
    availability: AvailabilityService
    bookings: BookingService
    workflow: LangGraphWorkflow
    redis: Redis


def create_runtime(settings: Settings | None = None) -> RuntimeContext:
    settings = settings or load_settings()
    logger = get_logger()
    tracer = build_tracer(settings)
    repository = PostgresRepository(settings.database_url)
    availability = AvailabilityService(repository)
    bookings = BookingService(repository)
    if (
        settings.understanding_provider != "gemma"
        or settings.decision_provider != "laya"
    ):
        raise ValueError(
            "Application operation requires Gemma and Laya; use the evaluation CLI for deterministic baselines"
        )
    redis_client = Redis.from_url(
        settings.chat_redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    interpreter = build_interpreter("gemma", settings.gemma_base_url)
    decision = build_decision("laya", settings.laya_base_url)
    audio_understanding = GemmaUnderstanding(
        settings.gemma_base_url,
        timeout_seconds=settings.telegram_audio_chunk_timeout_seconds,
    )
    transcribe = partial(
        audio_understanding.transcribe_recording,
        chunker=AudioChunker(
            max_input_bytes=settings.telegram_audio_max_bytes,
            max_decoded_bytes=settings.telegram_audio_max_decoded_bytes,
            decode_timeout_seconds=settings.telegram_audio_decode_timeout_seconds,
        ),
        processing_timeout_seconds=settings.telegram_audio_processing_timeout_seconds,
    )
    graph = build_graph(
        interpreter,
        decision,
        transcribe=transcribe,
        checkpointer=RedisCheckpointSaver(
            redis_client,
            ttl_seconds=settings.session_ttl_minutes * 60,
        ),
    )
    debug_event("app.runtime.configured", tracing=tracer is not None)
    return RuntimeContext(
        settings=settings,
        logger=logger,
        tracer=tracer,
        repository=repository,
        availability=availability,
        bookings=bookings,
        workflow=LangGraphWorkflow(graph),
        redis=redis_client,
    )


def close_runtime(runtime: RuntimeContext | None) -> None:
    if runtime and runtime.tracer is not None:
        try:
            runtime.tracer.flush()
        except Exception:
            pass
    if runtime is not None:
        runtime.redis.close()
