# Plan: Gemma and Laya as the defaults

Gemma 3n E2B understands every customer message: WhatsApp text, Telegram text, SMS, and short audio. Laya chooses the next action. The existing graph and `BookingService` still decide whether a write is allowed.

Neither model runs inside the FastAPI process. Each is a local HTTP adapter. If that server is down, the turn falls back and the failure is logged. The configured default remains Gemma and Laya.

## Target path

```text
WhatsApp, Telegram, SMS, or audio
        |
        v
Gemma 3n E2B
  text or SMS -> normalized request
  audio -> transcript + the same normalized request
        |
        v
Laya -> DecisionOutput
        |
        v
existing graph constraint
        |
        v
AvailabilityService / BookingService
```

Gemma must not choose a node, call a tool, or claim a slot is free. Laya must not write SQL, mint a token, or override the draft operation.

## Defaults

| Setting | Default | Fallback |
|---|---|---|
| `UNDERSTANDING_PROVIDER` | `gemma` | `deterministic`, then the current OpenAI interpreter only when explicitly selected |
| `DECISION_PROVIDER` | `laya` | `deterministic` |
| `GEMMA_BASE_URL` | `http://localhost:8081` | none |
| `LAYA_BASE_URL` | `http://localhost:8082` | none |

`LLM_PROVIDER=openai` remains an explicit text-understanding override. It is no longer the default.

## Phase 1 — One understanding contract

Add `app/llm/gemma.py` with one client used by every channel.

- `understand_text(message, context) -> SchedulingExtraction`
- `understand_audio(audio, context) -> NormalizedRequest`

The text method maps Gemma's JSON into the existing `SchedulingExtraction`. The audio method returns a transcript plus that same extraction, with `input_modality="audio"`. Invalid JSON or an unreachable server raises a typed provider error. It does not book.

Replace `FixtureAudioUnderstanding` as the production audio path. Keep the fixture for tests.

`app/graph/steps.py` already skips the text interpreter when a normalized audio request is present. SMS and chat text go through `interpret` and therefore through Gemma.

## Phase 2 — Laya decision adapter

Add `app/graph/laya.py`.

- Input: `NormalizedRequest` plus the workflow draft. No raw token, phone number, or token digest.
- Output: `DecisionOutput` with `next_action`, `confidence`, and `needs_human`.
- Allowed actions stay `BOOK`, `RESCHEDULE`, `CANCEL`, `CHECK_AVAILABILITY`, `FAQ`, `CONFIRM`, `ESCALATE`, and `LOOKUP_BOOKING`.

`route_turn` in `app/graph/steps.py` already rejects an action the draft does not allow. Do not remove that constraint. A Laya response of `RESCHEDULE` on a create draft still cannot reschedule.

## Phase 3 — Wire the defaults

In `app/runtime.py`, replace the hard-coded `DeterministicDecision()` and `build_interpreter(...)` selection.

```text
understanding = GemmaUnderstanding(GEMMA_BASE_URL) with deterministic fallback
decision = LayaDecision(LAYA_BASE_URL) with deterministic fallback
graph = build_graph(understanding, decision, checkpointer=...)
```

Log `understanding.fallback` or `decision.fallback` with the provider name and error type. Do not log the message text, phone, or token.

`app/config.py`, `.env.example`, and the API service in `docker-compose.yml` get the four settings above. Compose does not start the model servers in this phase. Document the expected local URLs.

## Phase 4 — Channel entry points

WhatsApp text already reaches `handle_turn`. SMS should be a new adapter with the same shape as `app/channels/whatsapp.py`: verify the provider, dedupe the provider message id, resolve the sender to `customer_id`, and call the same workflow. The SMS body is text, so it uses `understand_text`, not the audio method.

Audio attachments on WhatsApp call `understand_audio` and pass the normalized request into `handle_turn` with `skip_interpret` already supported by `TurnContext.normalized`.

Telegram uses the same text path when that adapter is added. This plan does not implement Telegram delivery.

## Phase 5 — Tests

Unit tests use fake HTTP responses, not a running model.

- Gemma text JSON becomes a `SchedulingExtraction` and does not call the booking port.
- Gemma audio JSON becomes a normalized request with a transcript. The graph routes it through Laya, not through a second interpreter.
- Laya `RESCHEDULE` on a create selection does not call reschedule.
- An unreachable Gemma URL falls back to the deterministic interpreter and logs the fallback.
- An unreachable Laya URL falls back to `DeterministicDecision` and logs the fallback.
- The raw management token is absent from both model requests.

The existing graph tests keep injecting `DeterministicInterpreter` and `DeterministicDecision` directly. Production defaults are covered by the runtime wiring test.

## Out of scope

- Training or fine-tuning Laya.
- Bundling Gemma weights in this repository.
- Letting either model compute availability or create the booking token.
- Live WhatsApp or SMS delivery. Those remain channel adapters in front of this path.

## Done when

- A text or SMS turn calls Gemma, then Laya, then the existing graph.
- An audio turn calls Gemma once and passes the transcript plus intent to Laya.
- `UNDERSTANDING_PROVIDER` defaults to `gemma` and `DECISION_PROVIDER` defaults to `laya`.
- A down model server degrades to the deterministic adapter for that turn only.
- Booking creation, the management token, and slot revalidation remain in `BookingService`.
