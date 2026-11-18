from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.graph.actions import SchedulingActions


@dataclass(frozen=True, repr=False)
class TurnInput:
    """Hold sensitive input only for the lifetime of a graph invocation."""

    text: str | None
    recording: bytes | None
    display_name: str | None


_TURN: ContextVar[tuple[SchedulingActions, TurnInput]] = ContextVar("scheduling_turn")


def current_actions() -> SchedulingActions:
    return _TURN.get()[0]


def current_input() -> TurnInput:
    return _TURN.get()[1]


@contextmanager
def bind_turn(actions: SchedulingActions, turn_input: TurnInput):
    """Keep request data available to nodes without adding it to checkpoints."""
    token = _TURN.set((actions, turn_input))
    try:
        yield
    finally:
        _TURN.reset(token)
