# Architecture

Telegram is the customer interface. `app/telegram_worker.py` validates private-chat sender identity, provisions the scoped customer, handles text/audio and saves replies in the durable update ledger.

`app/llm/gemma.py` decodes model responses for text and transcribes full recordings. `app/channels/audio_chunks.py` splits decoded audio near pauses with overlap and enforces size/time budgets. No chunk can execute a booking. The completed transcript enters the same scheduling interpreter as typed text.

`app/runtime.py` constructs Gemma and Laya directly through the same provider constructors used by evaluations. The normal app rejects deterministic overrides. Provider errors propagate to a safe retry response before writes. `app/graph/laya.py` proposes an action; graph constraints and domain services enforce the actual operation.

`app/graph/workflow.py` invokes one graph turn with a stable business/bot/user thread. Its Redis checkpoint owns draft, offered slots and at most 24 history messages. There is no separate draft/history/slot store. Private booking results stay in request-scoped `ChatPorts`, outside checkpoints. `lg:v2` isolates the new contract from older temporary state.

`app/chat_ports.py` holds the trusted business/customer identity and calls deterministic services. `app/booking.py` checks ownership and current policy inside transactions; `app/scheduling.py` handles local time, service assignment, notice, horizon, buffers and conflicts. PostgreSQL's exclusion constraint independently prevents overlaps. The business-row lock deliberately serializes writes per business for a simple idempotency boundary; this limits throughput and should be measured before changing.

`app/channels/telegram_repository.py` owns identity provisioning, update receipts, polling offsets and saved replies. Phone data never chooses the customer record. Duplicate updates do not rerun a turn; a crash with an unknown committed outcome yields a check-your-appointments response. Saved replies retry without rerunning booking operations. External reply delivery can duplicate after an ambiguous send timeout.

`app/reminders.py` writes demo reminders into an inbox using `SKIP LOCKED` and bounded retry. It has no graph dependency. `app/cli.py` is a local owner inspection command. `app/main.py` exposes health only.

`app/evals/` runs the real workflow with synthetic service boundaries or isolates extraction/routing. It does not establish database correctness: PostgreSQL/Redis integration tests cover that separately. Model training and servers are external to this repository.
