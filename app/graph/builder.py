from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.graph.context import TurnRuntime
from app.graph.decision import DeterministicDecision
from app.graph.state import SchedulingGraphState
from app.graph.steps import (
    clarify,
    escalate,
    execute,
    help_reply,
    load_context,
    lookup,
    merge_draft,
    persist,
    prepare_action,
    route_turn,
    search,
    select,
    understand_input,
)


def build_graph(
    interpreter,
    decision=None,
    threshold: float = 0.55,
    checkpointer=None,
    *,
    transcribe: Callable[[bytes, dict], str] | None = None,
):
    """Wire interpretation and routing to workflow steps with optional checkpoints."""
    decision_engine = decision or DeterministicDecision()

    def load_context_node(
        state: SchedulingGraphState, runtime: Runtime[TurnRuntime]
    ) -> dict:
        return load_context(state, runtime.context.actions)

    def understand_input_node(
        state: SchedulingGraphState, runtime: Runtime[TurnRuntime]
    ) -> dict:
        return understand_input(
            state, runtime.context.turn_input, interpreter, transcribe
        )

    def decide_node(state: SchedulingGraphState) -> dict:
        return route_turn(state, decision_engine, threshold)

    def clarify_node(
        state: SchedulingGraphState, runtime: Runtime[TurnRuntime]
    ) -> dict:
        return clarify(state, runtime.context.actions)

    def search_node(state: SchedulingGraphState, runtime: Runtime[TurnRuntime]) -> dict:
        return search(state, runtime.context.actions)

    def select_node(state: SchedulingGraphState, runtime: Runtime[TurnRuntime]) -> dict:
        return select(state, runtime.context.actions)

    def prepare_node(
        state: SchedulingGraphState, runtime: Runtime[TurnRuntime]
    ) -> dict:
        return prepare_action(state, runtime.context.actions)

    def execute_node(
        state: SchedulingGraphState, runtime: Runtime[TurnRuntime]
    ) -> dict:
        return execute(state, runtime.context.actions)

    def lookup_node(state: SchedulingGraphState, runtime: Runtime[TurnRuntime]) -> dict:
        return lookup(state, runtime.context.actions)

    def escalate_node(
        state: SchedulingGraphState, runtime: Runtime[TurnRuntime]
    ) -> dict:
        return escalate(state, runtime.context.actions)

    graph = StateGraph(SchedulingGraphState, context_schema=TurnRuntime)
    graph.add_node("load_context", load_context_node)
    graph.add_node("understand_input", understand_input_node)
    graph.add_node("merge_draft", merge_draft)
    graph.add_node("decide", decide_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("search", search_node)
    graph.add_node("select", select_node)
    graph.add_node("prepare_action", prepare_node)
    graph.add_node("execute", execute_node)
    graph.add_node("help", help_reply)
    graph.add_node("lookup", lookup_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("persist", persist)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "understand_input")
    graph.add_edge("understand_input", "merge_draft")
    graph.add_edge("merge_draft", "decide")
    graph.add_conditional_edges(
        "decide",
        lambda state: state["route"],
        {
            "clarify": "clarify",
            "search": "search",
            "select": "select",
            "help": "help",
            "lookup": "lookup",
            "escalate": "escalate",
            "prepare_action": "prepare_action",
        },
    )
    graph.add_conditional_edges(
        "select",
        lambda state: state["route"],
        {
            "prepare_action": "prepare_action",
            "persist": "persist",
            "clarify": "clarify",
        },
    )
    graph.add_conditional_edges(
        "prepare_action",
        lambda state: state["route"],
        {"execute": "execute", "persist": "persist", "clarify": "clarify"},
    )
    graph.add_edge("clarify", "persist")
    graph.add_edge("search", "persist")
    graph.add_edge("help", "persist")
    graph.add_edge("lookup", "persist")
    graph.add_edge("escalate", "persist")
    graph.add_edge("execute", "persist")
    graph.add_edge("persist", END)
    return graph.compile(checkpointer=checkpointer)
