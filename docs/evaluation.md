# Evaluation

The project uses two evaluation layers. `app.evals.runner` checks deterministic language extraction without PostgreSQL or model credentials. PostgreSQL integration tests check product outcomes through the real API, booking services, repositories, and reminder worker.

## Extraction benchmark

Run the offline benchmark with:

```bash
uv run python -m app.evals.runner
```

It prints pass/fail per scenario and writes JSON results under `.eval_runs/`. Scenarios cover service/date discovery, professional and local-time extraction, ordinal slot selection, customer contact extraction, cancellation intent, and rescheduling language. If `LLM_PROVIDER=openai` is configured, they exercise model extraction with deterministic fallback on provider errors.

## Product outcome evaluations

Integration tests require `TEST_DATABASE_URL` to point to a disposable PostgreSQL database whose name ends in `_test`; each test migrates, seeds, and clears it. Run them with:

```bash
TEST_DATABASE_URL=postgresql://user:password@localhost:5433/scheduling_test \
  uv run --extra dev pytest -q tests/integration
```

The API and persistence scenarios assert structured outcomes for date clarification, service-backed slots, idempotent replay, stale-slot rejection, prompt-injection resistance, booking conflicts, transaction rollback, concurrent creates, reminder retries, and reminder-reply rescheduling. The API reminder journey also checks the owner agenda and that the management token stays out of graph state and logs.

The browser walkthrough was exercised locally with PostgreSQL, FastAPI, Streamlit, and a reminder-worker delivery: search, book, confirm, inspect the owner agenda, receive a simulated reminder reply, and move the booking. Docker Compose startup could not be exercised because Docker was unavailable in the implementation environment.

Run the complete unit, API, evaluation, and integration suite with `uv run --extra dev pytest -q` after setting `TEST_DATABASE_URL`. Without it, PostgreSQL integration cases skip; unit and API contract checks still run.
