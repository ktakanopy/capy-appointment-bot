# General Service Scheduling Development Guidelines

Auto-generated from the general service scheduling MVP plan. Last updated: 2026-11-10

## Active Technologies

- Python 3.11+ with FastAPI, LangGraph, Pydantic v2, psycopg 3, Alembic, PostgreSQL, and Redis.
- Gemma text/audio understanding and Laya routing are required in the application; deterministic providers are explicit evaluation baselines.
- Langfuse Python SDK for optional observability.
- Redis-backed LangGraph checkpoints for short-lived conversation state; PostgreSQL for business, schedule, booking, and reminder data.

## Project Structure

```text
app/
alembic/
tests/
evals/
docs/
```

## Commands

- `uv run python -m app.evals.runner --understanding deterministic --routing deterministic` runs the offline bilingual task corpus.
- `uv run --extra dev pytest -q` runs the general scheduling contract and unit suite.
- `docker compose run --rm api python -m app.cli` prints the local owner agenda and reminder inbox.
- PostgreSQL transaction and full Compose walkthrough checks require a local database runtime.

## Code Style

- Python 3.11+; follow standard conventions and keep booking policy in deterministic application services.
- Keep raw booking management tokens out of graph state, model input, logs, traces, URLs, and errors.
- Keep migrations in `alembic/`; update them when persistence contracts change.

## Recent Changes

- Generalized the clinic prototype into a service scheduling MVP with PostgreSQL, deterministic availability, Telegram customer ownership, and a demo reminder worker.
- Made Telegram the only customer channel with text/audio and a local owner CLI; historical WhatsApp, SMS, and Streamlit implementations are in Git history.
- Laya is required in the app and bypassable in evaluations. Large bilingual datasets and Gemma/Laya fine-tuning remain follow-up work.

<!-- MANUAL ADDITIONS START -->
- Review and resolve outstanding `TODO` comments as follow-up work; keep them visible until handled.
<!-- MANUAL ADDITIONS END -->
