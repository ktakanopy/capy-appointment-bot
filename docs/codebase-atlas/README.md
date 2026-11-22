# CAPY codebase atlas

Open the [interactive atlas](index.html) directly in a browser. It starts on **CAPY architecture · Current runtime**: durable PostgreSQL inbox, one RabbitMQ notification queue, five initial processors with database ownership, and one independent sender. Use **Lost notification** for direct recovery and transaction boundaries, or **Independent progress** for Alice, Bob and a saved reply. Then open **Booking walkthrough** and use **Next step** across three turns. Open **Transcript** to read the customer messages and CAPY replies, or select a message to jump to its node.

Drag the canvas to pan, scroll to zoom, or click the minimap to jump. Selecting a node shows what it does, why it matters, and its highlighted source line. **View full file** opens the source in the page. Both code views use Python syntax highlighting. Use **Cmd/Ctrl+K** to find a step, file, class, or entry point.

## SVG diagrams

| Flow | What to follow |
| --- | --- |
| [Architecture overview](architecture-overview.svg) | Current runtime; [PNG preview](architecture-overview.png) for direct review |
| [Lost notification](notification-recovery.svg) | Post-commit publication failure, accepted intake and direct database recovery |
| [Independent progress](independent-progress.svg) | Slow Alice turn, concurrent Bob turn and an independent sender |
| [Booking walkthrough](walkthrough.svg) | One conversation from incoming Telegram updates through the committed appointment and final reply |
| [Telegram runtime](runtime.svg) | Post-commit RabbitMQ notifications, direct PostgreSQL recovery, chat ownership and independent delivery |
| [Conversation turn](conversation.svg) | LangGraph entry, model interpretation and routing, scheduling actions, and state |
| [Create a booking](booking.svg) | Search, select, then commit after a separate approval |
| [Failure paths](failure-paths.svg) | Intake rejection, expired state, retries, interrupted turns, model errors, consent, slot conflicts, and delivery outcomes |
| [Manage a booking](management.svg) | Cancel an appointment or move it to another time |
| [Evaluation framework](evaluation.svg) | Scenario inputs, graph execution, assertions, receipts, and HTML reports |
| [Test suites](tests.svg) | CI checks and representative unit, evaluation, and integration tests |

## Find a file or class

Use **File connections** to explore which cataloged files import a Python file and what that file imports. Click a neighbor to change the selected file, or click an import’s line number to open its source. The graph is generated from static import statements. The walkthrough shows runtime calls and handoffs.

The reference covers the Python modules and top-level classes under `app/` and `tests/`, the Alembic environment and migrations, the scenario YAML files, and the verification workflow. Each file highlights its main reading entry. Class rows point to the method that best explains its role or to the declaration for data classes, protocols and types.

- [Telegram and workers](files-telegram.svg)
- [Conversation, models and state](files-conversation.svg)
- [Scheduling and setup](files-scheduling.svg)
- [Domain contracts and database rows](files-data.svg)
- [Evaluation programs](files-evaluation.svg)
- [Verification and test files](files-tests.svg)

## Regenerate

Edit the [flow definitions](flows.json) and [source reference](files.json). The [Codebase Atlas skill](../../skills/codebase-atlas/SKILL.md) describes the reusable workflow. Run from the repository root.

```sh
python3 skills/codebase-atlas/scripts/render_atlas.py --web --project CAPY \
  --out docs/codebase-atlas docs/codebase-atlas/flows.json docs/codebase-atlas/files.json
```

The HTML includes snapshots of the referenced source files. Regenerate it after source changes to refresh the code and line numbers.

Five processors are an initial deployment configuration. [Verification evidence](../verification/rabbitmq.md) separates controlled tests and simulated inference/delivery from production capacity. The physical `telegram_conversations` table stores processing coordination and checkpoint linkage, not a transcript. Broker acknowledgments settle hints; `TelegramTurns.finish` completes inbox turns. The source reader resolves symbols and embeds fresh source snapshots on regeneration. Desktop and 390 × 844 navigation, search, inspector, source reader, transcript, retained bookmarks and Open SVG are covered by the recorded visual check.

## Writing card descriptions

Name the action in the title. In the description, say who acts and what happens to the message. Use the rationale to explain the outcome or the specific failure it prevents. Define terms such as a lease (temporary processing permission) when they are needed. Keep implementation names in the source links.
