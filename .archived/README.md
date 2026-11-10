# Archived implementations

These files are kept for reference and are not part of the running application.

| Path | Former purpose | Replaced by |
|---|---|---|
| `channels/whatsapp.py` | WhatsApp webhook, dedupe, and outbox helper | Telegram worker |
| `channels/sms.py` | Inbound SMS webhook | Telegram worker |
| `frontend/` | Streamlit customer and owner UI | `python -m app.cli` |
| `tests/*` | Tests for the archived channels and frontend | Remaining suite under `tests/` |

Restore with `git mv <path> <original-path>` and re-add the routes, config fields, dependencies, and compose service that the removal commit deleted.
