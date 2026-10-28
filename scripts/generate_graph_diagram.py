"""Generate the compiled-graph diagram in docs/architecture.md.

Usage:
    python scripts/generate_graph_diagram.py
"""

import re
import sys
from pathlib import Path

from app.graph.builder import build_graph
from app.graph.decision import DeterministicDecision
from app.llm.provider import DeterministicInterpreter

DOCS_PATH = Path(__file__).parent.parent / "docs" / "architecture.md"
MARKED_BLOCK = re.compile(
    r"<!-- compiled-graph:start -->.*?<!-- compiled-graph:end -->",
    re.DOTALL,
)


def main() -> None:
    graph = build_graph(DeterministicInterpreter(), DeterministicDecision())
    mermaid = graph.get_graph().draw_mermaid()
    new_block = f"<!-- compiled-graph:start -->\n```mermaid\n{mermaid}```\n<!-- compiled-graph:end -->"
    original = DOCS_PATH.read_text()
    if not MARKED_BLOCK.search(original):
        print(f"error: compiled-graph markers not found in {DOCS_PATH}", file=sys.stderr)
        sys.exit(1)
    DOCS_PATH.write_text(MARKED_BLOCK.sub(new_block, original, count=1))
    print(f"updated {DOCS_PATH}")


if __name__ == "__main__":
    main()
