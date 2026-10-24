from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.booking import BookingService
from app.chat_history import RedisChatStore
from app.config import Settings, load_settings
from app.graph.builder import build_graph
from app.graph.decision import DeterministicDecision
from app.graph.laya import FallbackDecision, LayaDecision
from app.graph.redis_checkpoint import RedisCheckpointSaver
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import build_interpreter
from app.observability import build_tracer, get_logger
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
    graph: object
    workflow: LangGraphWorkflow
    chat_store: RedisChatStore

    @property
    def session_slots(self):
        return self.chat_store.session_slots

    @property
    def pending_slots(self):
        return self.chat_store.pending_slots

    def create_session(self) -> tuple[str, str]:
        session_id = str(uuid.uuid4())
        self.chat_store.create_session(
            session_id,
            "Welcome. Tell me which service you'd like to book.",
        )
        return session_id, session_id

    def require_session(self, session_id: str) -> None:
        self.chat_store.require_session(session_id)


def understanding_choice(settings: Settings) -> str:
    """Keep an explicit OpenAI configuration ahead of the Gemma default."""
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return "openai"
    if settings.understanding_provider == "deterministic":
        return "deterministic"
    return "gemma"


def create_runtime(settings: Settings | None = None) -> RuntimeContext:
    settings = settings or load_settings()
    logger = get_logger()
    tracer = build_tracer(settings)
    repository = PostgresRepository(settings.database_url)
    availability = AvailabilityService(repository)
    bookings = BookingService(repository, settings.booking_token_secret)
    chat_store = RedisChatStore(
        settings.chat_redis_url,
        ttl_seconds=settings.session_ttl_minutes * 60,
    )
    choice = understanding_choice(settings)
    if choice == "openai":
        interpreter = build_interpreter("openai", settings.openai_api_key, settings.openai_model, logger=logger)
    elif choice == "deterministic":
        interpreter = build_interpreter("deterministic", None, settings.openai_model, logger=logger)
    else:
        interpreter = build_interpreter("gemma", None, settings.gemma_base_url, logger=logger)
    decision = DeterministicDecision()
    if settings.decision_provider != "deterministic":
        decision = FallbackDecision(LayaDecision(settings.laya_base_url), decision, logger)
    graph = build_graph(
        interpreter,
        decision,
        checkpointer=RedisCheckpointSaver(
            chat_store.client,
            ttl_seconds=settings.session_ttl_minutes * 60,
        ),
    )
    return RuntimeContext(
        settings=settings,
        logger=logger,
        tracer=tracer,
        repository=repository,
        availability=availability,
        bookings=bookings,
        graph=graph,
        workflow=LangGraphWorkflow(graph),
        chat_store=chat_store,
    )


def close_runtime(runtime: RuntimeContext | None) -> None:
    if runtime and runtime.tracer is not None:
        try:
            runtime.tracer.flush()
        except Exception:
            pass
    if runtime is not None and hasattr(runtime, "chat_store"):
        try:
            runtime.chat_store.close()
        except Exception:
            pass


def reset_runtime(target_app, settings: Settings | None = None) -> RuntimeContext:
    close_runtime(getattr(target_app.state, "runtime", None))
    runtime = create_runtime(settings=settings)
    target_app.state.runtime = runtime
    return runtime
