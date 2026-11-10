# General Service Scheduling Development Guidelines

Auto-generated from the general service scheduling MVP plan. Last updated: 2026-11-10

## Active Technologies

- Python 3.11+ with FastAPI, LangGraph, Pydantic v2, psycopg 3, Alembic, PostgreSQL, and Redis.
- OpenAI Python SDK for optional structured intent extraction; deterministic extraction remains available for local/offline use.
- Langfuse Python SDK for optional observability.
- Redis-backed LangGraph checkpoints for short-lived conversation state; PostgreSQL for business, schedule, booking, and reminder data.

## Project Structure

```text
app/
alembic/
tests/
docs/
.archived/
```

## Commands

- `uv run python -m app.evals.runner` runs the structured intent extraction scenarios.
- `uv run --extra dev pytest -q` runs the general scheduling contract and unit suite.
- `docker compose run --rm api python -m app.cli` runs the interactive customer and owner console.
- PostgreSQL transaction and full Compose walkthrough checks require a local database runtime.

## Code Style

- Python 3.11+; follow standard conventions and keep booking policy in deterministic application services.
- Keep raw booking management tokens out of graph state, model input, logs, traces, URLs, and errors.
- Keep migrations in `alembic/`; update them when persistence contracts change.

## Recent Changes

- Generalized the clinic prototype into a service scheduling MVP with PostgreSQL, deterministic availability, token-protected booking management, and a demo reminder worker.
- Made Telegram the only customer channel and added an interactive CLI for customer and owner flows; archived WhatsApp, SMS, and Streamlit under `.archived/`.
- Laya benchmarking and training remain a follow-up after the working product MVP.

<!-- MANUAL ADDITIONS START -->
- Review and resolve outstanding `TODO` comments as follow-up work; keep them visible until handled.
<!-- MANUAL ADDITIONS END -->
