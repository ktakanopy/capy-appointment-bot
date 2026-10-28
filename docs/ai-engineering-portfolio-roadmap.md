# AI Engineering portfolio roadmap

Reviewed: 2026-10-27. Reference branch: `portifolio`, commit `3017896`, including the working tree reviewed in this session.

Status: proposed portfolio priorities. This document records recommendations; it does not mark features as complete or replace feature requirements and implementation plans.

## Portfolio objective

Demonstrate an AI scheduling product whose language understanding, task success, reliability, latency, and cost can be measured and explained. Preserve deterministic availability, authorization, and booking policy as the application grows.

The strongest portfolio story is a customer completing a real scheduling journey, supported by reproducible evaluations and operational evidence.

## Reviewed baseline

- FastAPI, LangGraph, PostgreSQL, and Redis provide the application and conversation infrastructure.
- Booking services enforce transactional availability validation, idempotency, and overlap protection.
- Gemma understanding, optional OpenAI extraction, and Laya routing have adapters with deterministic fallback. Adapter availability does not establish real-model quality.
- The extraction benchmark contains six scenarios. API/database integration tests cover several business outcomes separately.
- Langfuse integration records events; model generations, usage, latency, and task-level quality need fuller instrumentation.
- Escalation currently records an event and returns a bounded response; there is no operational human handoff queue.
- Owner authentication is optional when `OWNER_API_TOKEN` is unset.
- Live Telegram delivery and real-audio readiness remain integration work. Audio-client and recording-splitting changes are in progress in the working tree; verify their current state before starting related implementation.
- WhatsApp remains wired into the application. Its webhook verifies subscriptions and handles inbound text, but live Meta outbound delivery is not connected.

## Channel scope decision

Lead the current portfolio walkthrough with Telegram and keep the existing WhatsApp adapter in the application. Do not remove or archive it as part of this work. Consider WhatsApp rollout or removal as a separate future decision. This commit preserves the current implementation in Git history so a later change can remove it from the app without losing the earlier version. Before any removal, account for the shared phone-normalization and dedupe/outbox helpers imported by SMS and Telegram.

Validation performed during the review: `tests/unit`, `tests/api`, and `tests/evals` passed, with **67 tests**. PostgreSQL integration tests, live messaging, and real model/audio endpoints were not validated in that run.

## Recommended priorities

| Priority | Delivery | Portfolio evidence |
|---|---|---|
| 1 | End-to-end evaluations and model comparisons | Reproducible quality, latency, and cost results |
| 2 | Real Telegram text and voice journey | A recorded customer journey using the deployed integration |
| 3 | Task and model observability | Traces and a dashboard explaining successes, failures, and spend |
| 4 | Grounded FAQ with an evaluated retrieval strategy | Source-backed answers and retrieval/answer-quality results |
| 5 | Operational human handoff | A request transferred, handled, and resumed with clear ownership |
| 6 | Reproducible deployment, CI, and failure recovery | Automated checks and a safe synthetic-data demo |
| 7 | Public engineering case study | Architecture decisions, measured outcomes, and known limitations |

### 1. Evaluate complete tasks and compare alternatives

- Use a shared evaluation configuration that can exercise the same understanding and decision providers as the runtime. The current extraction runner does not use the runtime's Gemma/Laya provider selection.
- Compare deterministic and model-based understanding independently from deterministic and Laya routing. Record provider/model, prompt version, dataset version, and application revision.
- Add multi-turn scenarios for booking, rescheduling, corrections, interruptions, FAQ-to-booking transitions, ambiguous language, and unauthorized requests. Include informal Portuguese and English where supported.
- Measure task success, extraction accuracy by field, routing accuracy, unauthorized actions, latency distribution, and cost per successfully completed task.
- Record primary-provider failures and fallback use separately. A successful fallback must not count as a successful prediction by the failed provider.
- Check business outcomes against persisted state. Use explicit rubrics and human-reviewed samples for open-ended answers.
- Make evaluation failures produce a failing process exit status before relying on the runner as a CI gate.

Completion evidence: a versioned dataset, reproducible commands, a results table, and a short analysis of representative failures. Set quality thresholds from a measured baseline.

### 2. Complete a real text and voice channel

Follow the existing [Telegram PRD](sdd/prd-telegram-text-voice.md) and [implementation plan](sdd/plan-telegram-text-voice.md) for feature scope and decisions.

- Demonstrate text/audio input, clarification, actual slot search, booking, and a later reschedule.
- Verify real endpoint compatibility and real recordings. Combine chunk transcripts into one workflow turn before performing a business write.
- Exercise duplicate updates, provider unavailability, identity boundaries, and process restart behavior.
- Preserve required booking contact information and keep raw management tokens outside model input, graph state, logs, traces, and URLs.

Completion evidence: a repeatable live walkthrough and a short video, with voice limitations and processing budgets documented.

### 3. Instrument quality, latency, and cost

- Correlate conversation turns, model generations, workflow steps, and business outcomes.
- Capture understanding, routing, retrieval, and execution durations; model/prompt versions; token usage; provider errors; and fallback frequency.
- Report task completion, clarification/escalation frequency, p50/p95 latency, and cost per successful task.
- Preserve redaction and avoid collecting sensitive conversation content merely to populate a dashboard.

Completion evidence: representative redacted traces and a dashboard that explains which configuration succeeds, how long it takes, and what it costs. Define infrastructure costs separately for self-hosted models rather than treating them as free.

### 4. Choose FAQ architecture through evaluation

- Keep price, duration, and availability backed by structured catalog/domain queries.
- Compare a small curated knowledge base supplied directly in context with a simple retrieval baseline for preparation instructions, policies, and broader business questions.
- Add hybrid retrieval or reranking when measured retrieval failures justify the complexity.
- Include identifiable sources, document versioning/update behavior, business-scoped retrieval, and an insufficient-evidence response.
- Evaluate retrieval relevance/recall, answer correctness, source support, and attempts to inject instructions through documents.

Completion evidence: a justified architecture choice and a reproducible evaluation containing answerable, unanswerable, stale-document, and cross-business cases.

### 5. Make human handoff operational

- Persist a handoff request with its reason, minimal context, status, and assigned operator.
- Provide an owner workflow to inspect, respond, and return control to the bot.
- Define bot behavior while a human owns the conversation.
- Validate escalation criteria against labeled scenarios. Fixed values such as `0.9` are routing heuristics, not calibrated probabilities.

Completion evidence: an unsupported request reaches an operator and resumes without conflicting bot actions or duplicate booking changes.

### 6. Demonstrate deployment and recovery

- Require owner authentication outside an explicit local demo mode; test customer and business isolation.
- Run PostgreSQL/Redis-backed integration checks in CI, alongside unit/API checks and reproducible offline evaluations. Keep live-provider evaluations explicit and budgeted.
- Exercise provider timeouts, duplicate delivery, retries, concurrent bookings, and process restarts. Verify business writes remain idempotent.
- Document startup, migrations, synthetic seeding, secrets, request limits, retention, and reset instructions for the demo.

Completion evidence: a reproducible environment, passing CI, and documented recovery demonstrations. Public demonstrations use synthetic data.

### 7. Publish the engineering case study

- Explain the customer problem and show the working journey early in the README.
- Keep implementation descriptions and default-provider configuration aligned with the code.
- Publish an architecture diagram, evaluation method/results, a short demo video, and limitations.
- Explain model and retrieval choices with measured tradeoffs. Consider Laya fine-tuning after a benchmark identifies useful improvements; retain a separate held-out evaluation set.

Completion evidence: a reviewer can understand the product, reproduce the evidence, and discuss the architecture from the repository alone.

## Related documents and references

- [Current architecture](architecture.md), [conversation graph](graph.md), [evaluations](evaluation.md), and [security](security.md).
- [Gemma/Laya direction](gemma-laya-default-plan.md) and [specification alignment](spec-alignment-plan.md) for existing design context; check historical findings against current code.
- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) for task outcomes, repeated trials, and grading strategies.
- [Langfuse: Model usage and cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking) for generation usage and cost instrumentation.
