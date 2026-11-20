# CAPY codebase atlas

Open the [interactive atlas](index.html) directly in a browser. Start with **Booking walkthrough** and use **Next step** to follow one Telegram conversation across three turns. Open **Transcript** to read the customer messages and CAPY replies, or select a message to jump to its node.

Drag the canvas to pan, scroll to zoom, or click the minimap to jump. Selecting a node shows what it does, why it matters, and its highlighted source line. **View full file** opens the source in the page. Both code views use Python syntax highlighting. Use **Cmd/Ctrl+K** to find a step, file, class, or entry point.

## SVG diagrams

| Flow | What to follow |
| --- | --- |
| [Booking walkthrough](walkthrough.svg) | One conversation from incoming Telegram updates through the committed appointment and final reply |
| [Telegram runtime](runtime.svg) | Webhook intake, RabbitMQ wake-ups, chat ownership, processing and reply delivery |
| [Conversation turn](conversation.svg) | LangGraph entry, model interpretation and routing, scheduling actions, and state |
| [Create a booking](booking.svg) | Search, select, then commit after a separate approval |
| [Failure paths](failure-paths.svg) | Intake rejection, expired state, retries, interrupted turns, model errors, consent, slot conflicts, and delivery outcomes |
| [Manage a booking](management.svg) | Cancel an appointment or move it to another time |

## Find a file or class

The reference covers the Python modules and top-level classes under `app/`, plus the Alembic environment and migrations. Each file highlights its main reading entry. Class rows point to the method that best explains its role or to the declaration for data classes, protocols and types.

- [Telegram and workers](files-telegram.svg)
- [Conversation, models and state](files-conversation.svg)
- [Scheduling and setup](files-scheduling.svg)
- [Domain contracts and database rows](files-data.svg)
- [Evaluation programs](files-evaluation.svg)

## Regenerate

Edit the [flow definitions](flows.json) and [source reference](files.json). The [Codebase Atlas skill](../../skills/codebase-atlas/SKILL.md) describes the reusable workflow. Run from the repository root.

```sh
python3 skills/codebase-atlas/scripts/render_atlas.py --web --project CAPY \
  --out docs/codebase-atlas docs/codebase-atlas/flows.json docs/codebase-atlas/files.json
```

The HTML includes snapshots of the referenced source files. Regenerate it after source changes to refresh the code and line numbers.
