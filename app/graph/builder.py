from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph.context import current_ports
from app.graph.decision import DeterministicDecision
from app.graph.state import SchedulingGraphState
from app.graph.steps import (
    clarify,
    escalate,
    execute,
    help_reply,
    interpret,
    load_context,
    lookup,
    merge_draft,
    persist,
    prepare_action,
    route_turn,
    search,
    select,
)


def build_graph(interpreter, decision=None, threshold: float = 0.55, checkpointer=None):
    decision_engine = decision or DeterministicDecision()

    def load_context_node(state: SchedulingGraphState) -> dict:
        return load_context(state, current_ports())

    def interpret_node(state: SchedulingGraphState) -> dict:
        return interpret(state, current_ports(), interpreter)

    def decide_node(state: SchedulingGraphState) -> dict:
        return route_turn(state, decision_engine, threshold)

    def clarify_node(state: SchedulingGraphState) -> dict:
        return clarify(state, current_ports())

    def search_node(state: SchedulingGraphState) -> dict:
        return search(state, current_ports())

    def select_node(state: SchedulingGraphState) -> dict:
        return select(state, current_ports())

    def prepare_node(state: SchedulingGraphState) -> dict:
        return prepare_action(state, current_ports())

    def execute_node(state: SchedulingGraphState) -> dict:
        return execute(state, current_ports())

    def help_node(state: SchedulingGraphState) -> dict:
        return help_reply(state, current_ports())

    def lookup_node(state: SchedulingGraphState) -> dict:
        return lookup(state, current_ports())

    def escalate_node(state: SchedulingGraphState) -> dict:
        return escalate(state, current_ports())

    def persist_node(state: SchedulingGraphState) -> dict:
        return persist(state, current_ports())

    graph = StateGraph(SchedulingGraphState)
    graph.add_node("load_context", load_context_node)
    graph.add_node("interpret", interpret_node)
    graph.add_node("merge_draft", merge_draft)
    graph.add_node("decide", decide_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("search", search_node)
    graph.add_node("select", select_node)
    graph.add_node("prepare_action", prepare_node)
    graph.add_node("execute", execute_node)
    graph.add_node("help", help_node)
    graph.add_node("lookup", lookup_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("persist", persist_node)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "interpret")
    graph.add_edge("interpret", "merge_draft")
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
        {"prepare_action": "prepare_action", "persist": "persist", "clarify": "clarify"},
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
