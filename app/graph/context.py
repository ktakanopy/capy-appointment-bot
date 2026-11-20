from __future__ import annotations

from dataclasses import dataclass

from app.graph.actions import SchedulingActions


@dataclass(frozen=True, repr=False)
class TurnInput:
    """Hold sensitive input only for the lifetime of a graph invocation."""

    text: str | None
    recording: bytes | None
    display_name: str | None


@dataclass(frozen=True, repr=False)
class TurnRuntime:
    """Keep request-specific services and raw input outside checkpointed state."""

    actions: SchedulingActions
    turn_input: TurnInput
