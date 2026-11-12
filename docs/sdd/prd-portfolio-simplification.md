# Portfolio simplification with Gemma audio and Laya routing

Status: requirements accompany the approved plan; implemented locally, automated checks passed, live verification pending. Created 2026-11-11.

Follow-up model-boundary decision: Gemma interprets the message without intent/action labels; Laya receives only the interpreted message and conversation context, then chooses the action. See the current [model contracts](../model-contracts.md), which supersede earlier model payload details in this record.

## Intent checkpoint

- **Intent:** simplify CAPY into a credible senior engineering portfolio project while preserving Telegram text and audio, Gemma understanding, and Laya routing.
- **Why:** remove obsolete interfaces, duplicate state, and unnecessary authorization machinery; make correctness and model quality easier to demonstrate.
- **Outcome:** one complete scheduling journey, with reproducible English/Portuguese evaluations that compare Laya routing against deterministic routing.
- **Confirmed:** keep audio, including the complete-recording path; use Gemma; require Laya for normal application operation; permit bypassing Laya explicitly during evaluation; prepare for a future large bilingual dataset and fine-tuning of both models.
- **Scope:** simplify identity, public interfaces, state, providers, deployment and documentation; fix privacy and evaluation defects; preserve booking invariants and delivery recovery.
- **Future work:** large dataset creation and actual fine-tuning. This change provides the evaluation foundation only.

The user requested the plan directly after the clarification batch. The following are conservative planning defaults, not separately confirmed answers: provider failures stop the turn without writes; existing database records are preserved; customer HTTP mutation interfaces are retired; owner operations use a local CLI; live text/audio validation is required before claiming release readiness. Implementation must not interpret this plan as permission to reset a database or send live Telegram messages without an authorized test destination.

## Original problem and source evidence

The observations below describe the checkout before simplification. References to deleted modules identify historical sources, not current entry points. See [current architecture](../architecture.md) and [verification evidence](../case-study.md) for the implemented system.

- Booking ownership is checked through both Telegram identity and management tokens. Customer-authorized operations mint a token internally and then validate it again (`app/booking.py`).
- Customer creation merges records by phone number. Removing contact proof without replacing that linkage would create an ownership risk.
- `/chat` redacts management tokens but can checkpoint personal information in the original message (`app/chat_adapter.py`, `app/graph/workflow.py`).
- Graph messages grow without a bound; a separate Redis history cap does not constrain them.
- Conversation storage is split between checkpoints and Redis draft/history/slot APIs.
- Runtime and evaluations select providers differently; extraction failures can leave the evaluation process successful.
- Legacy helpers and archived implementations obscure the supported path. Owner HTTP access is open when no owner token is configured.
- Real Telegram/audio validation and checked-in CI evidence are missing from the reviewed checkout.

## Requirements

| ID | Requirement and observable acceptance |
| --- | --- |
| R1 | Private Telegram chats support text, voice and audio attachments for search, booking, lookup, confirmation, cancellation and rescheduling. Unsupported input receives a bounded response. |
| R2 | Normal scheduling turns use Gemma understanding and Laya routing. Invalid/unavailable providers return a retry response and cannot commit a booking. Transport commands such as `/start` and validation failures need no model call. |
| R3 | Laya proposes an action; deterministic workflow and booking services enforce allowed operations, ownership, real availability and transaction rules. |
| R4 | Stable Telegram identity maps to a customer. Reads and mutations are scoped to customer and business. Phone numbers are contact data and never establish ownership or merge accounts. |
| R5 | Management-token customer interfaces are retired together with their consumers. Owner access remains local; any retained owner HTTP interface must require authentication. |
| R6 | One checkpoint owns conversation draft, offered slots and bounded sanitized history. Expiry starts a fresh conversation; restart preserves an unexpired conversation. |
| R7 | Contact information collected outside the model is excluded from model inputs and checkpoints. Recognized contacts and legacy tokens in free text are redacted before any checkpoint write. Documentation states the limits of free-text name detection. |
| R8 | Full recordings are decoded and transcribed before one scheduling interpretation and action. Failed chunks, late corrections, malformed audio or exhausted budgets cannot trigger a partial booking. |
| R9 | The evaluation runner uses shared provider construction and supports explicit Laya/deterministic routing on identical cases. Errors are visible and fail the run. No silent fallback may count as a model success. |
| R10 | A small versioned English/Portuguese text/audio corpus establishes extraction, routing and task-outcome checks. Reports identify dataset/model/configuration versions, language, modality and latency. |
| R11 | PostgreSQL overlap protection, transactional revalidation, idempotency, atomic reminders and Telegram receipt/reply recovery remain verified. |
| R12 | Existing database records and user working-tree edits are preserved. Migrations, retired interfaces and rollback limits are documented. |
| R13 | CI runs offline checks and real PostgreSQL/Redis tests; live model and Telegram checks are explicit and reported separately. |

## Boundaries and decisions

Gemma remains the sole learned understanding integration. Remove the optional OpenAI branch after migrating its consumers. Retain deterministic understanding as an explicit offline evaluation baseline. Laya remains required in the normal application; deterministic routing is an evaluation choice, not an automatic outage fallback.

Retain the existing Gemma `/understand` and Laya `/decide` adapters unless contract tests demonstrate a necessary change. Reuse LangGraph, PostgreSQL, Redis, Alembic and the current audio chunker. Keep the small PostgreSQL Telegram receipt/outbox mechanism. Do not introduce a generic provider registry, channel framework, new queue or training service.

Account migration preserves verified existing identity links. Old bookings without a trusted Telegram link remain owner-visible; do not automatically claim them by matching a typed phone number. New Telegram customers are created independently of phone uniqueness.

## Quality and non-goals

Quality evidence includes cross-account denial, concurrency, idempotent replay, migration preservation, checkpoint privacy, history bounds, restart, full-recording failure handling and bilingual model comparisons. Mocks validate contracts; real recordings and live endpoints validate actual speech behavior.

Excluded: large-scale data generation, training jobs, automatic collection of customer conversations for training, new WhatsApp/SMS/web interfaces, RAG, payments, calendar integration, a new human-handoff product and broad deployment infrastructure.

See [implementation plan](plan-portfolio-simplification.md) for work packages, contracts, ordering and verification.
