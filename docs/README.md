# Documentation

Start with the [repository README](../README.md) to run the offline booking example or the live Telegram demo. Then use the reading order below to follow a message through the code.

## Learn the code

| Guide | What it explains |
| --- | --- |
| [Architecture](architecture.md#how-to-read-the-code) | Reading order, system components, and booking sequences |
| [Codebase atlas](codebase-atlas/README.md) | Interactive flows and source-linked SVGs for entry points, booking turns, and recovery |
| [Source map](source-map.md) | Files to open for each part of the application |
| [Conversation graph](graph.md) | How one text or audio turn updates the draft and reaches a booking action |
| [Model contracts](model-contracts.md) | What Gemma interprets, what Laya decides, and what Python checks |
| [Telegram worker](telegram-worker.md) | Webhook intake, RabbitMQ notifications, chat leases, checkpoint recovery, and reply delivery |
| [Database](database.md) | Tables, relationships, ORM queries, and transaction boundaries |
| [Ownership and data handling](security.md) | Trusted identity, contact data, redaction, and retention |
| [Idempotency and recovery](idempotency.md) | Retry keys, duplicate updates, and uncertain outcomes |

## Evaluate and operate

| Guide | When to use it |
| --- | --- |
| [Scenario evaluation framework](evaluation-framework.md) | Write YAML conversations, run concurrent repetitions, prepare speech, judge replies, and read HTML reports |
| [JSONL evaluation](evaluation.md) | Isolate extraction and fixed-input routing, or run the compact regression corpus |
| [Deployment reference](production.md) | Configure worker roles, register bots, recover work, and interpret local queue benchmarks |
| [Scale study](system-design-100k.md) | Understand hypothetical workload estimates and measurements needed before claiming capacity |
| [Portfolio roadmap](ai-engineering-portfolio-roadmap.md) | Find remaining model-quality, dataset, and fine-tuning work |

## Requirements and historical evidence

These records explain earlier decisions and observations. Use the guides above for current setup and runtime behavior.

- [Evaluation requirements](sdd/prd-chatbot-evaluation.md) describe the behavior the framework should check. The [executable golden dataset](../evals/scenarios/golden.yaml) is the source for its example conversation and assertions.
- [Case study](case-study.md) records earlier local checks and two synthetic audio observations. Its test counts and dataset hashes belong to those recorded runs.
- [Repository history](history.md) links the historical requirements for Telegram and portfolio simplification. Completed implementation plans remain in Git history; new `docs/sdd/plan-*.md` files are ignored.
- [Historical study board](study/README.md) contains the November 12 schema review and its editable diagram. It predates the current worker architecture and later migrations.
