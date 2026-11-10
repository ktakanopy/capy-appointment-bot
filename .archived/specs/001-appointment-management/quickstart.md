# Quickstart: Conversational Appointment Management

## Prerequisites

- Python 3.11+
- Project dependencies installed from `pyproject.toml`

## Start the service

```bash
uvicorn app.main:app --reload
```

The API is expected to expose `POST /chat`.

## Example conversation: verify then list

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-session-1","message":"I want to see my appointments"}'
```

Expected behavior:

- response asks for the first missing verification field
- `verified` is `false`
- `current_operation` remains `list_appointments` or equivalent deferred intent

Continue with the same `session_id`:

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-session-1","message":"Ana Silva"}'

curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-session-1","message":"11999998888"}'

curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-session-1","message":"1990-05-10"}'
```

Expected behavior after the last turn:

- patient becomes verified
- deferred list action resumes automatically
- response includes only that patient's appointments

## Example conversation: confirm by ordinal reference

After listing appointments in the same session:

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-session-1","message":"confirm the first one"}'
```

Expected behavior:

- the first appointment in the current list is resolved
- the appointment is confirmed if it is patient-owned and confirmable
- repeated requests return a stable idempotent response

## Example conversation: cancel then reroute back to list

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-session-1","message":"cancel the first one"}'

curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-session-1","message":"show me my appointments again"}'
```

Expected behavior:

- cancellation succeeds only for the verified patient's appointment
- the second request follows the latest valid intent without restarting
- the refreshed list reflects the cancellation outcome

## Automated validation

Run the full test suite:

```bash
pytest
```

Recommended focused runs during implementation:

```bash
pytest tests/unit
pytest tests/graph
pytest tests/api
```
