# Quickstart: Frontend, LLM, and Returning Memory

> Historical planning artifact: this quickstart includes exploratory remembered-identity setup that was later removed from the delivered product scope.

## Prerequisites

- Python 3.11+
- project dependencies installed
- an OpenAI API key for the default provider
- Langfuse credentials if tracing is enabled

## Environment

Set the default provider credentials:

```bash
export OPENAI_API_KEY=your_key_here
export OPENAI_MODEL=gpt-4o-mini
```

Enable tracing when available:

```bash
export LANGFUSE_PUBLIC_KEY=your_public_key
export LANGFUSE_SECRET_KEY=your_secret_key
export LANGFUSE_HOST=https://cloud.langfuse.com
```

## Start the backend

```bash
uv run uvicorn app.main:app --reload
```

## Start the frontend

```bash
uv run streamlit run frontend/streamlit_app.py
```

Open the Streamlit UI in the browser and start a new session.

## Demo flow

1. Start a conversation in the UI.
2. Ask to see appointments.
3. Complete verification if needed.
4. Confirm or cancel an appointment.
5. Start a fresh session and verify that remembered identity restores patient
   context when still valid.
6. Clear remembered identity and confirm the workflow falls back to normal
   verification.

Manual API restore and forget examples:

```bash
curl -X POST http://localhost:8000/sessions/new \
  -H 'Content-Type: application/json' \
  -d '{"remembered_identity_id":"<remembered-identity-id>"}'

curl -X POST http://localhost:8000/remembered-identity/forget \
  -H 'Content-Type: application/json' \
  -d '{"remembered_identity_id":"<remembered-identity-id>"}'
```

## Offline evaluation

Run the curated scenario suite:

```bash
uv run python -m app.evals.runner
```

Expected output:

- per-scenario result
- pass or fail summary
- judge explanation for failing or ambiguous scenarios

## Focused validation

```bash
uv run pytest tests/unit
uv run pytest tests/graph
uv run pytest tests/api
uv run pytest tests/evals
```
