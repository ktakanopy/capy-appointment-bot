# Telegram scheduling through text and voice

Status: requirements reflect the user's decisions on 2026-10-27. Telegram, identity persistence, and audio integration are implemented in the working tree; no migration, bot worker, real Telegram call, or real audio provider check has been run.

## Intent checkpoint

- **User intent:** make Telegram the customer chatbot interface, accepting text and long voice messages. WhatsApp is the next channel.
- **Why:** customers should complete scheduling conversations in a messaging app without a Streamlit chatbot.
- **Desired outcome:** `@capy_appointment_bot` receives real messages and returns scheduling results through the existing graph and deterministic application services.
- **Current problem:** the repository has a Gemma audio client wrapper and channel examples, but no live Telegram adapter or complete long-recording path.
- **Confirmed scope:** retain required phone numbers; support text and voice; split recordings when required by model limits; reuse the configured Gemma endpoint. No audio-server implementation or Streamlit chatbot development.
- **Acceptance:** verified phone-backed customers can search, choose a slot, book, and manage their bookings by text or voice. Interpret each complete recording before any booking write. Provider failures and incomplete recordings must not trigger writes.
- **Constraints:** model output never grants customer ownership or decides booking availability. Keep credentials, raw management tokens, audio bytes, and contact details out of model authorization, logs, and traces. Respect the hosted Telegram download limit and measured processing budgets.
- **Confirmed identity preference:** display a Telegram username when present. Use numeric Telegram user ID internally and keep phone numbers as booking contact information.

## Problem and value

The customer conversation needs to run in Telegram now and later WhatsApp. The existing Streamlit chat surface is not the requested delivery interface. Supporting actual voice requires downloading and decoding Telegram media, calling an existing audio-capable endpoint, preserving the complete message's meaning, and connecting it to the shared booking workflow.

## Solution

Add an optional Telegram polling worker around the existing graph, `ChatPorts`, availability service, and booking service. The worker handles identity, contact sharing, durable update receipts, text/audio routing, and safe text replies. Use the supplied bot credential from local environment configuration only.

For voice, decode incrementally and divide the recording into model-sized windows. Reassemble ordered transcripts, interpret the complete message once, and then execute one workflow turn. Chunk-level extraction must not execute actions: a later correction can change the user's request.

## User stories and requirements

| ID | User story / requirement | Observable acceptance | Plan owner |
| --- | --- | --- | --- |
| R1 | As a customer, I can use Telegram text to perform existing scheduling actions. | Search, slot selection, create, confirm, cancel, reschedule, and catalog help use the shared workflow and existing policy. | WP3, WP5 |
| R2 | As a customer, I can send a voice note or supported audio attachment. | A real recording is decoded and understood through the existing Gemma endpoint; its complete transcript produces one workflow turn. | WP1, WP4, WP5 |
| R3 | As a customer, I can send recordings longer than one model window. | Recordings over 30 seconds are chunked; ordering, overlap, dates, negation, and late corrections are preserved. No silent trimming or action based on a prefix. | WP4, WP6 |
| R4 | As a customer, I retain phone-based booking contact information. | Required-phone validation remains; own-contact sharing verifies the contact before existing customer linkage. Typed/spoken phone or username alone grants no access. | WP2, WP5 |
| R5 | As a returning customer, my account survives username changes. | Numeric Telegram identity resolves the same authorized customer; another account cannot view or change that customer's bookings. | WP2, WP6 |
| R6 | As a customer, retries do not duplicate booking operations. | Durable update receipts and stable business idempotency keys survive restart. Saved reply delivery is retried without rerunning the graph. Unknown committed outcomes are reconciled. | WP2, WP3, WP5 |
| R7 | As a customer, unavailable or incomplete audio yields a clear failure. | Unsupported/corrupt media, failed chunks, silence, provider downtime, or exceeded processing/context budgets yield a retry/type/shorter-message response and no booking write. | WP1, WP4, WP5 |
| R8 | As an operator, I can run Telegram without requiring Streamlit. | An optional worker entry point and environment configuration are documented; existing API, owner tools, and other channels remain compatible. | WP3, WP6 |
| R9 | As an operator, I can handle credentials and customer data safely. | Tokens and token-bearing URLs are absent from source, logs, traces, and replies. Audio/transient files are cleaned up. Text/media retention is documented and bounded. | WP2–WP6 |
| R10 | As a customer, I can switch between text and voice in a conversation. | A checkpointed audio→text→audio sequence uses only each turn's current request, preserving the booking draft without stale normalized input. | WP4, WP6 |

## Boundaries and ownership

- **AI/provider integration (WP1/WP4):** validate the existing endpoint, normalize media in the client where required, transcribe chunks, assemble complete text, and validate final extraction. Own no server implementation or model hosting.
- **Persistence/domain (WP2):** own Alembic migration and repository/service changes for channel identity and update outcomes. Phone remains mandatory; no nullable-phone migration or historical customer backfill is needed.
- **Channel transport/runtime (WP3):** own polling intake, API calls, actual byte limits, timeouts, credentials, and restart behavior.
- **Channel orchestration (WP5):** own onboarding, trusted customer linkage, `TurnContext`, stable idempotency keys, and channel-safe replies.
- **Quality/deployment (WP6):** own focused regression evidence, real audio/bot walkthrough, worker image media dependency, configuration and documentation.
- **Application services:** keep availability, booking authorization, overlaps, and token generation in their existing deterministic services.

Work-package labels refer to the [implementation plan](plan-telegram-text-voice.md); they are not registered tracker or SDD task IDs.

## Implementation decisions

1. Use private Telegram chats and a single long-polling worker initially. Report an existing webhook conflict without changing it automatically.
2. Keep `(business_id, bot_id, telegram_user_id)` as the channel identity. Username is optional display metadata. Require a self-contact match before linking a phone-backed customer; do not silently replace existing linkage.
3. Use the standard hosted Bot API's documented 20 MB download allowance. There is no new 30-second message-duration cap. Chunking cannot bypass a download rejected by Telegram. A local Bot API deployment is a separate extension. [Telegram download and local-server contracts](https://core.telegram.org/bots/api#using-a-local-bot-api-server).
4. Keep each chunk inside the existing endpoint's verified audio window; Gemma 3n documents a 30-second window. Use silence boundaries and limited overlap where appropriate. [Gemma 3n guide](https://developers.googleblog.com/en/introducing-gemma-3n-developer-guide/).
5. Combine all chunk transcripts before a single final scheduling extraction. Preserve slot index and request-scoped contact capture; ignore model-supplied authorization and identity fields. Fail safely if the full message cannot fit the verified interpreter context.
6. Use bounded streaming/incremental decoding, temporary storage cleanup, per-stage timeouts, and an explicit total processing budget. Derive production values from measured hardware behavior rather than promising unlimited recording length or compute.
7. Reuse the configured Gemma API without server changes. If it cannot process real audio, report voice unavailability and retain text operation; voice readiness remains incomplete.
8. Use a small PostgreSQL update ledger and saved reply outcome. Stable update-derived idempotency keys protect business writes; ambiguous send timeouts can still duplicate external replies.
9. Initial engineering defaults retain existing English text replies and booking actions. Evaluate English and Portuguese speech; broader language guarantees and localization are outside this feature. These defaults remain visible in plan review.
10. Telegram first; WhatsApp launch follows separately. Reuse the same workflow/audio boundary later without adding a generic channel framework now. Existing Streamlit owner tools need no changes for this feature.

## Testing and quality decisions

- Contract tests cover Telegram parsing, contact ownership, retry behavior, provider errors, normalized-request clearing, and slot selection.
- Real audio fixtures cover codec decoding, recordings over one model window, overlap, intentional repetitions, late negation, corrupt media, failed middle chunks, and processing/context limits.
- PostgreSQL integration covers required-phone compatibility, identity linkage, update deduplication, idempotency reconciliation, migration, and restart behavior.
- A real existing Gemma endpoint and real Telegram bot walkthrough are required for voice readiness. Mocked HTTP responses alone do not establish audio understanding.
- Include two customers, changed/missing usernames, text/voice switching, delivery failure after booking commit, and model downtime.
- Run the existing unit/contract suite and extraction evaluations as release checks after implementation.

## Rollout and rollback

Apply the identity/update migration, validate existing model capabilities, then enable the optional worker. Keep credentials in ignored local environment/deployment secret configuration. Roll back by stopping the worker; do not delete identity/update history, reset webhooks, or discard pending updates automatically.

## Out of scope

Streamlit chatbot development, deletion of existing owner/frontend tools, WhatsApp rollout in this delivery, audio/model-server implementation or hosting, local Telegram Bot API hosting, TTS, model training, group chats, public webhooks, Telegram reminders, broad localization, and unrelated refactors/TODO resolution.

## Risks and review state

- The existing custom Gemma endpoint's real audio compatibility has not been demonstrated. Server work is excluded, so an incompatibility must be reported as a voice-readiness blocker.
- The Telegram polling worker, contact verification, update/reply ledger, Gemma chunking adapter, and deployment configuration are implemented. The companion [implementation plan](plan-telegram-text-voice.md) records the work-package status and unverified integration checks.
- Long recordings increase latency and may exceed processing or text-context budgets even within the transport size limit. Communicate failure and ask for shorter messages rather than executing partial content.
- Existing customer upsert-by-phone must never turn unverified contact extraction into account ownership.
- SDD and Plannotator tools are unavailable in this session, so no mutable tracker task state or external review is claimed. Implementation status and remaining integration checks are recorded in the companion plan.
