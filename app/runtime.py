from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from langgraph.checkpoint.memory import InMemorySaver

from app.booking import BookingService
from app.config import Settings, load_settings
from app.graph.builder import build_graph
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import build_interpreter
from app.models import AvailabilitySlot, DomainError
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
    sessions: dict[str, float] = field(default_factory=dict)
    session_slots: dict[str, list[AvailabilitySlot]] = field(default_factory=dict)
    pending_slots: dict[str, AvailabilitySlot] = field(default_factory=dict)

    def cleanup_sessions(self) -> None:
        cutoff = time.time() - self.settings.session_ttl_minutes * 60
        expired = [session_id for session_id, last_seen in self.sessions.items() if last_seen < cutoff]
        for session_id in expired:
            self.sessions.pop(session_id, None)
            self.session_slots.pop(session_id, None)
            self.pending_slots.pop(session_id, None)

    def create_session(self) -> tuple[str, str]:
        self.cleanup_sessions()
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = time.time()
        return session_id, session_id

    def require_session(self, session_id: str) -> None:
        self.cleanup_sessions()
        if session_id not in self.sessions:
            raise DomainError("session_not_found", "Session not found. Start a new session.", 404)
        self.sessions[session_id] = time.time()


def create_runtime(settings: Settings | None = None) -> RuntimeContext:
    settings = settings or load_settings()
    logger = get_logger()
    tracer = build_tracer(settings)
    repository = PostgresRepository(settings.database_url)
    availability = AvailabilityService(repository)
    bookings = BookingService(repository, settings.booking_token_secret)
    interpreter = build_interpreter(settings.llm_provider, settings.openai_api_key, settings.openai_model)
    graph = build_graph(interpreter, checkpointer=InMemorySaver())
    return RuntimeContext(
        settings=settings,
        logger=logger,
        tracer=tracer,
        repository=repository,
        availability=availability,
        bookings=bookings,
        graph=graph,
        workflow=LangGraphWorkflow(graph),
    )


def close_runtime(runtime: RuntimeContext | None) -> None:
    if runtime and runtime.tracer is not None:
        try:
            runtime.tracer.flush()
        except Exception:
            pass


def reset_runtime(target_app, settings: Settings | None = None) -> RuntimeContext:
    close_runtime(getattr(target_app.state, "runtime", None))
    runtime = create_runtime(settings=settings)
    target_app.state.runtime = runtime
    return runtime
