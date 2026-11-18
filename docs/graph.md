# Conversation graph

A turn loads its catalog and checkpoint draft. The `understand_input` node accepts typed text or asks Gemma to transcribe a complete recording. It redacts the resulting text, then uses Gemma to rewrite the message in simple terms and extract booking facts. Python merges those facts into the draft, then asks Laya to choose the next action from the interpreted message and draft context. Deterministic checks enforce required details and valid references before execution. Execution calls owned domain operations through `SchedulingActions`, and persistence adds the reply to bounded history.

```mermaid
flowchart TD
    START([Start turn]) --> load_context["load_context<br/>Load business catalog"]
    load_context --> understand_input["understand_input<br/>Transcribe audio when present<br/>Redact text and extract booking facts"]
    understand_input --> merge_draft["merge_draft<br/>Update the conversation draft"]
    merge_draft --> decide{"decide<br/>Laya proposes an action;<br/>Python constrains the route"}

    decide --> clarify["clarify<br/>Ask for missing details"]
    decide --> search["search<br/>Find and offer available slots"]
    decide --> select["select<br/>Validate an offered slot choice"]
    decide --> prepare_action["prepare_action<br/>Check customer, booking and slot"]
    decide --> help["help<br/>Answer from the catalog"]
    decide --> lookup["lookup<br/>List the customer's appointments"]
    decide --> escalate["escalate<br/>Reply with supported capabilities"]

    select --> prepare_action
    select --> clarify
    select --> persist
    prepare_action --> execute["execute<br/>Call owned booking services"]
    prepare_action --> clarify
    prepare_action --> persist

    clarify --> persist["persist<br/>Append reply to bounded history"]
    search --> persist
    help --> persist
    lookup --> persist
    escalate --> persist
    execute --> persist
    persist --> END([Return reply])
```

The arrows mirror the graph edges in [builder.py](../app/graph/builder.py). `decide` calls Laya and applies deterministic constraints in `route_turn`. Preparation helpers run inside `prepare_action`; they are not separate graph nodes. Redis checkpoints state throughout graph execution; the `persist` node appends the reply to history.

Use the compiled graph to check the arrows against the implementation after changing a route.
This prints Mermaid using the offline providers.

```sh
uv run --locked python scripts/generate_graph_diagram.py
```

Laya's HTTP body has exactly two keys: `message` (Gemma's interpreted text) and `context` (the current `WorkflowDraft`). The original message, Gemma facts object, intent labels, confidence guesses and customer identity are not additional routing inputs. Laya owns the action decision, including `CLARIFY` when the meaning remains ambiguous. Python checks ownership, offered slots and current booking policy. See the [model contracts](model-contracts.md) for seed-based payload examples.

Telegram downloads a bounded recording and passes it to the workflow in memory. The workflow passes raw text, audio, the display name and scheduling actions through LangGraph’s per-run `context`. Nodes that need them declare a `Runtime[TurnRuntime]` argument. The input node writes sanitized text to graph state, and the checkpointer saves that state using the thread ID passed in `config`. [Runtime context](../app/graph/context.py) · [Graph builder](../app/graph/builder.py) · [Invocation](../app/graph/workflow.py)

The graph waits for all chunks before interpreting a voice message, so a late correction can change the request Laya sees. A failed transcription stops the turn before routing. Gemma and Laya are required for normal turns. Offline text evaluations use deterministic test providers.

`prepare_action` dispatches to `_prepare_create`, `_prepare_reschedule` or `_prepare_booking_status_change`. These functions check the required slot and booking references before routing to `execute`. `CustomerSchedulingActions` connects those workflow operations to services for the trusted customer; `SchedulingActions` is the protocol also implemented by evaluation fixtures.

Redis holds the draft, offered slots, sanitized interpretation and decision fields, and at most 24 messages. Raw inputs and the display name used for redaction are scoped to the invocation. The node appends one sanitized user message after interpretation succeeds. Each worker attempt writes to a separate thread. A successful turn publishes the thread reference on the PostgreSQL conversation row.

An unsupported response is not an operational human handoff; there is no staffed handoff queue.
