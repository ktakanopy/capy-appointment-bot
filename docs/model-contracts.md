# Model contracts

The application uses this flow for both English and Portuguese:

`Text or full audio transcript → Gemma interpretation → Laya routing → Python services`

This repository contains HTTP clients, not model servers or weights. The external Gemma and Laya servers must implement these contracts. Servers using the old Gemma `intent` response or Laya `request`/`draft` input need a coordinated update. Invalid responses raise a provider error; there is no silent compatibility fallback. Redis uses `lg:v3` so temporary conversations start fresh with this contract. PostgreSQL records are unchanged.

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

## Python enforces execution rules

The graph checks required details, matched catalog references and offered slots. A choice among booking references cannot be used as a choice among availability slots. Booking services recheck customer ownership, current policy, conflicts and idempotency inside PostgreSQL transactions. Gemma and Laya do not decide whether a slot is free or authorize a customer.

The normal application requires both models. The evaluation CLI can replace Laya with the deterministic router while retaining Gemma text/audio interpretation. Fixed-input routing evaluations use a labelled `interpreted_message` and draft context; they invoke neither Gemma nor audio transcription. See [evaluation](evaluation.md) for the distinction between model quality and fixture-based regression checks.
