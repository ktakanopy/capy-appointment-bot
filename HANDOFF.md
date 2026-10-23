# Project Handoff

**Goal**
Continue helping the user understand and develop this local appointment scheduling bot, preserving the current working tree and explaining changes in Portuguese when appropriate.

**Current State**
- Python 3.11+ app: FastAPI API, Streamlit customer/owner UI, LangGraph intent extraction, PostgreSQL booking/catalog persistence, Redis chat session/history state.
- Chat supports availability search, slot selection and booking, plus token-protected confirm, cancel, and reschedule. Demo data is synthetic (Demo Studio, Massage, two professionals); no general business Q&A/RAG or WhatsApp/SMS integration. Reminders are simulated in a demo inbox.
- Deterministic extraction works offline. The local ignored `.env` now has `LLM_PROVIDER=openai`; `OPENAI_API_KEY` is nonempty, but its validity has not been checked. Do not print or record its value. Compose passes these variables to `api` and defaults `OPENAI_MODEL` to `gpt-4o-mini`.
- The user has been learning the codebase and prefers direct explanations of what changed and why, in Portuguese.

**Changed Files / Config**
- `README.md`: added “What the bot can do” and its demo limitations. The file already contained other uncommitted updates before this addition.
- `.env` (ignored): commented out the deterministic provider line and set `LLM_PROVIDER=openai`.
- Existing dirty worktree also includes changes to `.env.example`, `app/chat_helpers.py`, `app/config.py`, `app/graph/*`, `app/llm/*`, `app/main.py`, `app/models.py`, `app/runtime.py`, `docker-compose.yml`, `pyproject.toml`, and untracked `app/chat_history.py`. Preserve these; they implement recent structured extraction and Redis history work. Inspect diffs before changing them.
- `HANDOFF.md`: this handoff summary.

**Useful Commands**
- Start demo: `docker compose up --build`; UI at `http://localhost:8501`, API docs at `http://localhost:8000/docs`.
- Apply changed OpenAI environment to an existing stack: `docker compose up -d --force-recreate api`.
- Stop stack: `docker compose down`.

**Validation**
- No tests or runtime verification were run in this conversation. Only inspected README/config and checked the Compose environment mappings.

**Open Tasks**
- No outstanding implementation task was requested. If asked to validate OpenAI operation, check container logs/health without exposing environment secrets; the configured key has not been verified.

**Do Not**
- Do not revert or overwrite the existing dirty worktree.
- Do not include secrets or management tokens in notes, logs, or chat output.
