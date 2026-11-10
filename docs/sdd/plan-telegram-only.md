# Plan: Telegram-only refactor

Status: executed 2026-11-10. Archived files live under `.archived/`; target branch: `portifolio`.

Execution notes:

- WhatsApp, SMS, and Streamlit were moved to `.archived/` instead of deleted, with their tests under `.archived/tests/`.
- `uv run pytest -q`: 59 passed, 11 skipped.
- `TEST_DATABASE_URL=... uv run pytest tests/integration -q`: 11 passed.
- `uv run python -m app.evals.runner`: all 11 scenarios pass.
- CLI smoke test: booking created, management token printed, `/owner` shows the booking with `reminder=pending`; reschedule now seeds the service and professional from the authorized booking.
- Compose: api, PostgreSQL, and Redis healthy; no frontend service remains.
- Live Telegram text/voice was not run because no bot token or Gemma server was available.

## Goal

- Make Telegram the single customer channel.
- Archive the WhatsApp and SMS channels under `.archived/`.
- Remove the Streamlit frontend for now; test through the Telegram worker and a new CLI.
- **Do not change the audio path.**

## Non-goals

- No changes to `app/channels/audio.py`, `app/channels/audio_chunks.py`, the Gemma transcription path, or the Telegram audio flow.
- No changes to scheduling, booking, idempotency, token, or reminder logic except the two open review items listed in Phase 4.

## Preconditions

- [ ] Tag the current state for easy rollback: `git tag pre-telegram-only` (optional but cheap).
- [ ] Have a Telegram bot token and, for voice, a compatible Gemma server available for manual testing.

## Phase 1 — Archive WhatsApp and SMS

- [x] Move `canonical_phone` out of `app/channels/whatsapp.py` into a new `app/channels/phone.py`; update the import in `app/telegram_worker.py:20`.
- [x] Move `app/channels/whatsapp.py` and `app/channels/sms.py` to `.archived/channels/`. `MemoryDeduper`/`MemoryOutbox` are only used by SMS, so they go too.
- [x] Remove routes `GET /webhooks/whatsapp`, `POST /webhooks/whatsapp`, `POST /webhooks/sms` and their imports (`app/main.py:15-16`, `app/main.py:269-310`).
- [x] Remove config fields and env reads: `whatsapp_app_secret`, `whatsapp_verify_token`, `whatsapp_phone_number_id`, `sms_webhook_secret` (`app/config.py:23-25`, `:31`, `:81-83`, `:89`).
- [x] Remove `WHATSAPP_*`, `SMS_WEBHOOK_SECRET`, and `FRONTEND_API_BASE_URL` from `.env.example` and the compose environment blocks.
- [x] Move `tests/unit/test_whatsapp_channel.py` and `tests/unit/test_sms_channel.py` to `.archived/tests/`.

## Phase 2 — Remove Streamlit

- [x] Delete `frontend/` from the active tree (moved to `.archived/frontend/`).
- [x] Remove the `frontend` service and port `8501` from `docker-compose.yml` (`:148` onward).
- [x] Remove `COPY frontend ./frontend` from `Dockerfile:16`.
- [x] Remove `streamlit` from `pyproject.toml` dependencies and refresh `uv.lock`.
- [x] Move `tests/unit/test_frontend_api_client.py` to `.archived/tests/`.
- [x] Update `README.md`: drop the Streamlit URLs and demo journey, point the demo at the CLI and Telegram instead, and remove the `frontend/` project-map entry.

## Phase 3 — Add a CLI test surface

- [x] Add `python -m app.cli`:
  - [x] Build the runtime with `create_runtime(load_settings())` and call `runtime.create_session()`.
  - [x] Loop `input()` and call `handle_chat(runtime, ChatRequest(...), None)`.
  - [x] Print the assistant `response` and any returned management token clearly (this also addresses the "token never surfaced" problem for the local demo).
  - [x] Accept `/token <manage_token>` to attach a capability to later turns, `/owner` to print the agenda and demo reminders, and `/quit`.
  - [x] Map a numbered reply to `selected_slot_id` when the response offers slots.

## Phase 4 — ChatGPT (Codex) PR review comments

Disposition of the eight findings from PR #18 (commit `eeda9a1`):

| # | Sev | Finding | Status | Action in this plan |
|---|---|---|---|---|
| 1 | P2 | Model date string ("tomorrow") reaches parsing and returns 500 (`app/main.py`) | Already fixed | None — `_parse_date` catches `ValueError` (`app/graph/steps.py:28`) |
| 2 | P2 | Reminder text renders stored `timestamptz` in UTC, not business timezone (`app/reminders.py`) | Fixed in this change | Formats in the business timezone with `ZoneInfo` |
| 3 | P1 | Chat-created management token lost across Streamlit reruns (`frontend/streamlit_app.py`) | Resolved by removal | CLI prints the token (Phase 3) |
| 4 | P2 | Chat keeps showing stale slots after an empty search (`frontend/streamlit_app.py`) | Resolved by removal | None |
| 5 | P2 | Owner agenda has no reminder status (`app/models.py`) | Fixed in this change | Added `OwnerBooking.reminder_status`; CLI `/owner` displays it |
| 6 | P1 | Clarification loses the selected service across turns (`app/graph/builder.py`) | Already fixed | None — `merge_draft` carries the draft and missing field |
| 7 | P2 | Service resolved by substring before exact match (`app/main.py`) | Already fixed | None — `_choose_service` matches exact, casefolded (`app/chat_helpers.py`) |
| 8 | P2 | Reschedule ignores the requested professional (`app/main.py`) | Already fixed | None — `draft.professional_id` is used in search |

Remaining work items carried into this plan: findings **2** and **5**.

- [x] Fix reminder timezone formatting (`app/reminders.py`).
- [x] Add reminder status to the owner agenda response and CLI output (`app/models.py`, `/owner/*`).

## Validation

- [x] `uv run --extra dev pytest -q` passes after test removal.
- [x] `uv run python -m app.evals.runner` still passes.
- [x] `docker compose up --build` starts api, PostgreSQL, Redis, and the reminder worker with no frontend service.
- [ ] `docker compose --profile telegram up -d --build` runs the worker; a text and a voice message both complete a booking. (Not run: no bot token or Gemma server.)
- [x] `docker compose run --rm api python -m app.cli` walks a booking and prints the token; `/owner` shows the reminder.
- [x] `grep -ri "whatsapp\|sms\|streamlit" app scripts docker-compose.yml pyproject.toml` returns no active-code references.

## Files touched

- Archived: `app/channels/whatsapp.py` → `.archived/channels/`, `app/channels/sms.py` → `.archived/channels/`, `frontend/` → `.archived/frontend/`, three tests → `.archived/tests/`
- Add: `app/channels/phone.py`, `app/cli.py`, `app/owner.py`, `.archived/README.md`
- Edit: `app/main.py`, `app/config.py`, `app/telegram_worker.py`, `app/reminders.py`, `app/models.py`, `app/repositories.py`, `app/chat_adapter.py`, `app/chat_ports.py`, `app/graph/contracts.py`, `app/graph/steps.py`, `app/graph/decision.py`, `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`, `.env.example`, `README.md`, `docs/architecture.md`, `docs/graph.md`, `docs/architecture-overview.svg`, `tests/unit/test_gemma_laya.py`

## Open questions

- [x] Keep the `/owner/*` endpoints without a UI? Yes; the CLI surfaces them.
- [x] Delete `frontend/` outright or archive it? Archive it under `.archived/frontend/`.
