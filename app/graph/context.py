from __future__ import annotations

from contextvars import ContextVar

from app.graph.actions import SchedulingActions

_ACTIONS: ContextVar[SchedulingActions | None] = ContextVar(
    "scheduling_actions", default=None
)


def current_actions() -> SchedulingActions:
    actions = _ACTIONS.get()
    if actions is None:
        raise RuntimeError("scheduling actions are not bound")
    return actions


def bind_actions(actions: SchedulingActions):
    return _ACTIONS.set(actions)


def reset_actions(token) -> None:
    _ACTIONS.reset(token)
