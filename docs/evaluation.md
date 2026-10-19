# Evaluation

`app.evals.runner` replays a small, deterministic extraction benchmark without requiring PostgreSQL or model credentials. The scenarios cover service/date discovery, professional and local-time extraction, ordinal slot selection, customer contact extraction, cancellation intent, and rescheduling language.

Run it with:

```bash
uv run python -m app.evals.runner
```

The runner prints pass/fail per scenario and writes JSON results under `.eval_runs/`. When `LLM_PROVIDER=openai` is configured, the same scenarios exercise model extraction; provider failures fall back to the deterministic interpreter.

## Product behavior to review manually

The extractor benchmark does not establish booking correctness. A local product review should also cover:

1. Search an actual seeded service and confirm every offered slot falls within business hours.
2. Book a slot and verify the owner agenda shows the same time and status.
3. Retry a create request with the same idempotency key and confirm it returns the same booking and token.
4. Attempt to use a stale or conflicting slot and confirm the write is rejected without an overlapping booking.
5. Reschedule to a blocked/conflicting time and verify the original booking and reminder remain unchanged.
6. Cancel a booking and verify its pending reminder is canceled.
7. Wait for a due reminder, send a reply as a new `/chat` turn with the token separate, and reschedule.
8. Verify raw management tokens and customer contact data do not appear in logs, traces, graph input, or checkpoints.

The repository's pre-existing clinic pytest suite has not been migrated to this feature and was not run for this implementation. Do not treat that suite as current evidence for the scheduling product.
