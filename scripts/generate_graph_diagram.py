"""Print a Mermaid diagram from the compiled conversation graph."""

from app.graph.builder import build_graph
from app.graph.decision import DeterministicDecision
from app.llm.provider import DeterministicInterpreter


def main() -> None:
    graph = build_graph(DeterministicInterpreter(), DeterministicDecision())
    print(graph.get_graph().draw_mermaid())


if __name__ == "__main__":
    main()
