from __future__ import annotations

from contextvars import ContextVar

from app.graph.ports import WorkflowPorts


_PORTS: ContextVar[WorkflowPorts | None] = ContextVar("workflow_ports", default=None)


def current_ports() -> WorkflowPorts:
    ports = _PORTS.get()
    if ports is None:
        raise RuntimeError("workflow ports are not bound")
    return ports


def bind_ports(ports: WorkflowPorts):
    return _PORTS.set(ports)


def reset_ports(token) -> None:
    _PORTS.reset(token)
