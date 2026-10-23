"""Generate the Mermaid diagram in docs/graph.md from the live compiled graph.

Usage:
    uv run python scripts/generate_graph_diagram.py

The script compiles the LangGraph graph with stub dependencies, calls
draw_mermaid(), and replaces the first ```mermaid ... ``` block in
docs/graph.md with the generated output.  Everything outside that block is
left untouched.
"""

import re
import sys
from pathlib import Path

from app.graph.builder import build_graph
from app.graph.decision import DeterministicDecision
from app.llm.provider import DeterministicInterpreter

DOCS_PATH = Path(__file__).parent.parent / "docs" / "graph.md"
MERMAID_BLOCK = re.compile(r"```mermaid\n.*?```", re.DOTALL)


def main() -> None:
    graph = build_graph(DeterministicInterpreter(), DeterministicDecision())

    mermaid = graph.get_graph().draw_mermaid()
    new_block = f"```mermaid\n{mermaid}```"

    original = DOCS_PATH.read_text()
    if not MERMAID_BLOCK.search(original):
        print(f"error: no mermaid block found in {DOCS_PATH}", file=sys.stderr)
        sys.exit(1)

    updated = MERMAID_BLOCK.sub(new_block, original, count=1)
    DOCS_PATH.write_text(updated)
    print(f"updated {DOCS_PATH}")


if __name__ == "__main__":
    main()
