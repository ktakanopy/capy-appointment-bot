# Architecture

Telegram is the customer interface. `app/telegram_worker.py` validates private-chat sender identity, provisions the scoped customer, handles text/audio and saves replies in the durable update ledger.

`app/llm/gemma.py` rewrites a message in simple language and extracts booking facts for Python, without assigning an intent or next action. It also transcribes full recordings. `app/channels/audio_chunks.py` splits decoded audio near pauses with overlap and enforces size/time budgets. No chunk can execute a booking. The completed transcript enters the same interpreter as typed text.

`app/runtime.py` constructs Gemma and Laya directly through the same provider constructors used by evaluations. The normal app rejects deterministic overrides. Provider errors propagate to a safe retry response before writes. `app/graph/laya.py` sends only Gemma's interpreted `message` and draft `context` to Laya. Laya proposes the action; Python enforces required facts, valid references and booking authorization. External servers must follow the [model contracts](model-contracts.md).

`app/graph/workflow.py` invokes one graph turn with a stable business/bot/user thread. Its Redis checkpoint owns draft, offered slots and at most 24 history messages. There is no separate draft/history/slot store. Private booking results stay in request-scoped `CustomerSchedulingActions`, outside checkpoints. `lg:v3` isolates the interpreted-message contract from older temporary state.

`app/graph/actions.py` defines `SchedulingActions`, the protocol for operations the workflow may request. `CustomerSchedulingActions` in `app/customer_scheduling_actions.py` implements that contract for the trusted business/customer identity and calls deterministic services. Evaluation fixtures implement the same contract without real booking writes. `app/booking.py` checks ownership and current policy inside transactions; `app/scheduling.py` handles local time, service assignment, notice, horizon, buffers and conflicts. PostgreSQL's exclusion constraint independently prevents overlaps. The business-row lock deliberately serializes writes per business for a simple idempotency boundary; this limits throughput and should be measured before changing.

`app/channels/telegram_repository.py` owns identity provisioning, update receipts, polling offsets and saved replies. Phone data never chooses the customer record. Duplicate updates do not rerun a turn; a crash with an unknown committed outcome yields a check-your-appointments response. Saved replies retry without rerunning booking operations. External reply delivery can duplicate after an ambiguous send timeout.

`app/reminders.py` writes demo reminders into an inbox using `SKIP LOCKED` and bounded retry. It has no graph dependency. `app/cli.py` is a local owner inspection command that constructs only a PostgreSQL repository. `app/main.py` exposes health only and constructs its own Redis client. Neither entry point constructs Gemma, Laya or the conversation graph; only the Telegram worker needs the full application runtime.

`app/evals/` runs the real workflow with synthetic service boundaries or isolates extraction/routing. It does not establish database correctness: PostgreSQL/Redis integration tests cover that separately. Model training and servers are external to this repository.


## How to read the code

Follow one user request rather than reading files alphabetically. Use “Book a relaxation massage tomorrow”, then “1”, as the example.

1. Start at `TelegramWorker._run_turn` in `app/telegram_worker.py`. It turns trusted channel identity and sanitized text into `TurnContext`, creates request-scoped `CustomerSchedulingActions`, calls the workflow, and returns reply text. Read `_process_one` next to see how text and complete audio transcripts reach that entry point.
2. Read `TurnContext`, `WorkflowDraft`, and `TurnResult` in `app/graph/contracts.py`: current-turn input, the scheduling information remembered across turns, and the response. `SchedulingInterpretation` in `app/llm/schemas.py` contains Gemma's plain-language message and booking facts. Laya receives the interpreted message and draft context, then returns `DecisionOutput`.
3. Read `LangGraphWorkflow.handle_turn` in `app/graph/workflow.py`. It prepares graph input and uses the stable thread ID for checkpoint persistence. `app/runtime.py` shows how Gemma, Laya, Redis and domain services are connected.
4. Read graph edges in `app/graph/builder.py`, then follow the relevant functions in `app/graph/steps.py`: `load_context` → `interpret` → `merge_draft` → `route_turn`. For the first example message, follow `search`; for the numbered reply, follow `select` → `prepare_action` → `execute`. All successful terminal paths reach `persist`.
5. Read `GemmaUnderstanding.interpret` and `LayaDecision.decide` in `app/llm/gemma.py` and `app/graph/laya.py`. Gemma interprets the message and supplies booking facts; Laya alone chooses the action from that interpreted message and context. `_constrain` in `steps.py` applies deterministic workflow restrictions. For voice, read `transcribe_recording` after the text path is clear.
6. Follow a write from `CustomerSchedulingActions.create_booking` into `BookingService.create` in `app/booking.py`. `app/scheduling.py` calculates slots and revalidates them inside writes. `app/repositories.py` reads business data; `alembic/versions/` defines database constraints and migrations. Read `_owned_row` to understand management authorization.
7. Read `tests/integration/test_workflow_booking_journey.py` as an executable booking→confirmation→reminder→rescheduling→cancellation example. It uses real PostgreSQL services with deterministic model substitutes. Then read `app/evals/runner.py`: `run_scenarios` evaluates the corpus, `_evaluate_case` follows one conversation, `_read_turn_input` handles text/audio, and `_score_turn` compares predictions with labels. Use `evals/data/smoke-text.jsonl` to follow Laya on/off experiments.

For each step ask: What does it receive? What does it return? What does it change? Which part is allowed to make that decision? Start with responsibilities and data shapes; inspect Redis serialization, audio splitting and receipt SQL when you need those details.

To learn the flow locally without a bot or model servers, run `uv run --extra dev pytest -q tests/unit/test_conversation_graph.py`, then `uv run python -m app.evals.runner --understanding deterministic --routing deterministic`. Read one test together with the corresponding graph nodes before opening the next layer.


### Following a Telegram update

`TelegramWorker.run` manages startup checks, the single-worker lock and the polling cycle. `_poll_updates` saves updates before advancing the offset; `_process_pending_updates` claims them before handling them. `_deliver_ready_replies` sends persisted responses and preserves retry order.

For one claimed update, `_process_one` reads in order:

1. `_private_message` validates the channel and recorded sender.
2. `_ensure_customer` resolves the trusted Telegram account.
3. `_reply_to_message` handles `/start`, optional contact sharing, or a scheduling request.
4. `save_reply` persists the response before delivery.

For scheduling input, `_message_input` returns typed text or the complete audio transcript. `_run_turn` sanitizes it, builds `TurnContext` and calls the workflow once. Provider errors produce a safe retry message; an unknown outcome asks the user to inspect their appointments. Whitespace-only text gets the supported-input hint. No audio chunk runs a booking action.

The transport boundary is `TelegramBotApi` in `app/channels/telegram.py`: it builds requests, decodes bounded responses and returns safe error codes with retry metadata. `TelegramRepository.ensure_customer_for_sender` creates or finds the customer for the numeric sender inside one transaction. `ready_replies` returns the oldest waiting reply across the bot; a delayed reply blocks newer ones. Delivery retries send the saved text without invoking the graph again.
