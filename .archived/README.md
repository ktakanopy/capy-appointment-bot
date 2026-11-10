# Archived material

Historical implementations, specifications, and assets. Nothing here is imported, built, or run by the application.

| Path | Former purpose | Current replacement |
|---|---|---|
| `channels/whatsapp.py` | WhatsApp webhook, dedupe, outbox helper | Telegram worker |
| `channels/sms.py` | Inbound SMS webhook | Telegram worker |
| `frontend/` | Streamlit customer and owner UI | `python -m app.cli` |
| `tests/*` | Tests for the archived channels and frontend | Suite under `tests/` |
| `assets/capy.png` | Streamlit branding | Unused |
| `assets/demo-chat-flow.gif` | Old chatbot walkthrough | Outdated; do not use as current evidence |
| `reference/task_description.pdf` | Original assignment brief | Historical reference |
| `reference/Service Scheduling Agent — Architecture.md` | Original design input | Current `docs/architecture.md` |
| `reference/Service Scheduling Agent — Project Specification.md` | Original product spec | Current README and roadmap |
| `reviews/engineering-review.md` | Earlier engineering review | Historical reference |
| `specs/001-appointment-management/` | Original clinic specification | Historical reference |
| `specs/002-frontend-llm-memory/` | Frontend and remembered-identity exploration | Historical reference |

Restore an implementation with `git mv <path> <original-path>` and re-add the routes, configuration, dependencies, and Compose service that the removal commit deleted. The archived spec trees contain links written for their original location.
