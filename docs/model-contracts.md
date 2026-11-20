# Model contracts

The application uses this flow for both English and Portuguese:

`Text or full audio transcript → Gemma interpretation → Laya routing → Python services`

Invalid responses raise a provider error. Temporary conversations use the Redis namespace `lg:v3`.

## Local model service

The [Apple Silicon demo](../README.md#run-the-local-models-on-apple-silicon) serves both endpoints on port 8081. It downloads the pinned Gemma 4 E2B IT LiteRT bundle for text and audio and the Laya Multilingual MLX checkpoint for routing. To use different checkpoints, pass `--gemma-model /path/to/model.litertlm` and `--laya-model /path/to/laya-mlx-checkpoint` to `serve`.

To use separate model servers, set `GEMMA_BASE_URL` and `LAYA_BASE_URL` to endpoints the Telegram worker can reach.

## Gemma interprets meaning

For text, `POST /understand` receives `modality: "text"`, the sanitized `message`, and a `context` containing catalog names, today's date, whether choices exist, and bounded conversation history. For example, the default seed supplies `Relaxation massage`, `Ana Costa`, `Rui Mendes`, and today's date in `America/Sao_Paulo`.

Configure the Gemma server to rewrite the complete request in short, simple language. Preserve the customer's question, negation, corrections and uncertainty. Resolve relative dates only when the supplied date and meaning make them unambiguous. Do not invent missing details, turn an uncertain request into a definite instruction, classify intent or choose a next action.

For “Uh, tomorrow, could I get a relaxation massage with Ana Costa?”, assuming today is November 11, 2026, a valid response is:

```json
{
  "message": "I want to book a relaxation massage with Ana Costa on 2026-11-12.",
  "service_name": "Relaxation massage",
  "professional_name": "Ana Costa",
  "local_date": "2026-11-12",
  "local_time": null,
  "slot_index": null
}
```

`message` is required and must be nonblank, at most 64,000 characters. The other fields are optional booking facts for Python to match against the business catalog and merge into the draft. Unknown facts remain `null`; ambiguous dates must not become a guessed date. `slot_index` refers only to a numbered choice in the supplied conversation context. Unknown response fields, including `intent` and `next_action`, are rejected.

For an ambiguous question such as “Maybe cancel it, or move it?”, the message must preserve that uncertainty. Laya can then choose `CLARIFY`.

Audio requests to `/understand` retain `modality: "audio"`, `audio_base64` and the same safe context. Each response supplies a nonblank `transcript`. All chunks must succeed and combine into the complete transcript before the text interpretation request runs. No chunk chooses an action or starts a booking turn.

## Laya chooses the next step

`POST /decide` receives exactly two top-level fields: `message` and `context`. The message is Gemma's sanitized interpretation, not the original utterance. Context is the current `WorkflowDraft`; it contains the conversation's matched booking facts and progress. It does not contain customer identity, contact details, catalog objects or the raw transcript.

For the seed example above, the first-turn request is:

```json
{
  "message": "I want to book a relaxation massage with Ana Costa on 2026-11-12.",
  "context": {
    "operation": "none",
    "service_id": "service-demo",
    "service_name": "Relaxation massage",
    "professional_id": "professional-demo-ana",
    "local_date": "2026-11-12",
    "earliest_local_time": null,
    "offered_slot_ids": [],
    "pending_slot_id": null,
    "confirmation_requested_for": null,
    "missing": "none",
    "active_booking_id": null,
    "candidate_booking_ids": [],
    "escalated": false,
    "invalid_date": false
  }
}
```

There is no additional intent, extracted-facts object, modality or invented understanding confidence in this request. `operation` records the operation selected on an earlier turn by the router; it is `none` before the first decision. Booking facts reach the context only through Python's draft merge.

A suitable response for this example would be:

```json
{
  "next_action": "CHECK_AVAILABILITY",
  "confidence": 0.9,
  "needs_human": false,
  "reason_code": null
}
```

Allowed actions are `CHECK_AVAILABILITY`, `BOOK`, `RESCHEDULE`, `CANCEL`, `CONFIRM`, `FAQ`, `LOOKUP_BOOKING`, `CLARIFY` and `ESCALATE`. Confidence belongs to Laya's action decision and must be between zero and one. An uncertain meaning can produce `CLARIFY`; low-confidence or unsupported requests receive the existing bounded unsupported response. There is no staffed handoff queue.

For a new booking, `BOOK` first means “choose one of the offered times.” After that choice, Python stores its ID in both `pending_slot_id` and `confirmation_requested_for` and asks the customer to approve the exact service, professional, date, time and business timezone. The next invocation gives Laya the draft with a pending-confirmation marker. Laya should return `BOOK` for a clear approval, but Python also checks the latest sanitized user message against a narrow English/Portuguese phrase list. Both checks must agree, and the marker must still point to the offered slot. A `CANCEL`, `CONFIRM`, `RESCHEDULE`, unclear response, or changed time cannot create or change a booking while this new-booking consent is being resolved. `CONFIRM` remains the action for confirming an appointment that already exists. [Consent guard and route](../app/graph/steps.py#L341) · [Exact phrase check](../app/graph/consent.py#L49)

After the create transaction succeeds, Python formats the success response from the returned booking record. It converts the saved start time into the business timezone and resolves the saved service and professional IDs to catalog names, so the reply reports the booking that was actually committed. Telegram's optional sender `language_code` chooses English or Portuguese for the consent prompt and success reply; unsupported or missing locales default to English. [Reply formatter](../app/graph/booking_messages.py#L14) · [Telegram locale mapping](../app/telegram_worker.py#L177)

## Python enforces execution rules

The graph checks required details, matched catalog references and offered slots. A choice among booking references cannot be used as a choice among availability slots. Booking services recheck customer ownership, current policy, conflicts and idempotency inside PostgreSQL transactions. Gemma and Laya do not decide whether a slot is free or authorize a customer.

The normal application requires both models. The evaluation CLI can replace Laya with the deterministic router while retaining Gemma text/audio interpretation. Fixed-input routing evaluations use a labelled `interpreted_message` and draft context; they invoke neither Gemma nor audio transcription. The YAML scenario framework separately runs complete sequential conversations and captures both validated model objects and deterministic booking effects. See the [evaluation framework](evaluation-framework.md) and the [existing JSONL evaluation path](evaluation.md) for the distinction between these checks.
