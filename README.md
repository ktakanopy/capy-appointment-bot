# CAPY — Telegram scheduling with text and audio

A portfolio scheduling assistant for service businesses. Gemma rewrites text or complete voice recordings into simple language; Laya receives that interpreted message and the conversation context, then proposes the next action. Deterministic Python services authorize changes, calculate availability and commit bookings in PostgreSQL.

<p align="center"><img src="docs/assets/capy.png" alt="CAPY on Telegram. Find available times, book and look up upcoming appointments, confirm, cancel or reschedule, and ask about services and prices. Send text, voice notes or audio." width="640"></p>

Customers can search, choose a slot, book, look up appointments, confirm, cancel and reschedule in private Telegram chats. Numeric Telegram identity owns the customer's bookings. A phone number is optional contact information; providing someone else's number grants no access.

## Run the offline example

Run the scheduling workflow locally with scripted model responses and simulated booking services. You can try it without Telegram, PostgreSQL, Redis or model downloads.

```sh
uv sync --locked --extra dev
uv run python -m app.evals.runner --understanding deterministic --routing deterministic
```

The runner prints each case's status and the path to a JSON report. Look for `en-booking` to follow a massage booking from the initial request to choosing tomorrow and selecting the first offered time. The case checks that CAPY creates exactly one booking. [Follow the booking through the code](docs/architecture.md#how-to-read-the-code).

## Try the Telegram voice demo

Create a private `.env` from [`.env.example`](.env.example) and set `TELEGRAM_BOT_TOKEN` for a test bot.

### Run the local models on Apple Silicon

Use `uv` to download Gemma (2.59 GB) and Laya (678 MB) once into `.local-models/`, then start the model server.

```bash
uv run --locked \
  --with litert-lm-api==0.19.1 \
  --with laya-mlx==0.6.0 \
  python -m app.local_models download
uv run --locked \
  --with litert-lm-api==0.19.1 \
  --with laya-mlx==0.6.0 \
  python -m app.local_models serve
```

Check `http://localhost:8081/health`. [Model setup options](docs/model-contracts.md#local-model-service)

### Start the Telegram demo

In a second terminal, start the demo with Docker Compose.

```bash
docker compose --profile telegram \
  up --build
```

Compose sets up the demo studio and connects the bot to `host.docker.internal:8081`. Check `http://localhost:8000/health`.

### Make a test booking

In a private Telegram chat with the test bot, send a voice note asking for massage times tomorrow. Answer any follow-up, then reply with an offered number to book it.

Check the booking and the demo reminder inbox in the owner CLI.

```bash
docker compose run --rm api \
  python -m app.cli
```

Run the [bilingual evaluation](#evaluate-with-and-without-laya) to check routing and booking writes before using the bot with customers.

## Flow and guarantees

![CAPY runtime architecture. The Telegram worker handles text and audio, then runs the LangGraph workflow. Gemma transcribes and interprets messages, Laya proposes an action, and Python services enforce booking rules. Redis stores conversation state. PostgreSQL stores bookings, Telegram receipts and demo reminders.](docs/assets/architecture.svg)

Availability results are references, not reservations. Booking writes revalidate policy inside their transaction. PostgreSQL rejects overlapping active bookings; idempotency protects retries. Booking and reminder changes commit atomically. Redis holds a bounded, temporary conversation draft, offered slots and history. Telegram receipts preserve update deduplication and saved replies across restarts.

See the [database guide](docs/database.md) for the PostgreSQL tables, their relationships and the booking transaction.

## Evaluate with and without Laya

```bash
# Real text and speech through Gemma, then Laya:
uv run python -m app.evals.runner --dataset evals/data/smoke.jsonl --understanding gemma --routing laya
# Same cases through Gemma with deterministic routing:
uv run python -m app.evals.runner --dataset evals/data/smoke.jsonl --understanding gemma --routing deterministic
```

Use `--gemma-revision` and `--laya-revision` to identify checkpoints. Reports include dataset/asset hashes, predictions, errors, per-stage latency and language/modality summaries. Failed cases cause a nonzero exit. `--layer extraction`, `--layer routing` and `--layer workflow` isolate different questions; fixed-input routing uses each turn's labelled `interpreted_message` and draft context without invoking Gemma.

The seed corpus has 16 English/Portuguese text task cases and two synthetic speech cases. It checks the evaluation pipeline and common regressions. A larger grouped train/dev/test dataset will support fine-tuning and held-out comparisons. [Evaluation guide](docs/evaluation.md) · [Case study](docs/case-study.md)

## Verify

```bash
# Disposable PostgreSQL and Redis; does not use application data volumes:
docker compose -p capy-simplification-test -f docker-compose.test.yml run --build --rm checks
# Remove the disposable containers when finished:
docker compose -p capy-simplification-test -f docker-compose.test.yml down
```

For host tests, provide a disposable PostgreSQL `TEST_DATABASE_URL` ending in `_test` and `TEST_REDIS_URL`, then run `uv run --extra dev pytest -q`. Without those variables, integration tests skip. CI sets them and requires integration checks. ffmpeg is required for audio tests.

Optional self-hosted Langfuse:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml --profile telegram up --build
```

Set `TRACING_ENABLED=true` when configuring tracing. Observability services have separate storage from business data.

## Migration and current limits

Existing databases need a coordinated cutover: stop old workers, back up PostgreSQL, then upgrade with the new application. Migration `0003_telegram_ownership` preserves bookings and verified identity links while retiring token columns. Legacy customers without a trusted link remain owner-visible. It never claims them by phone. Reverting token removal requires restoring the matching backup and application. The message/context model contract starts temporary conversations fresh under checkpoint namespace `lg:v3`; business records are unchanged.

The smoke corpus exercises the local models with synthetic data. A live Telegram walkthrough requires a test bot and test chat.

Read the runtime guides for [architecture](docs/architecture.md), [privacy and ownership](docs/security.md) and [idempotency](docs/idempotency.md). Project background is in the [implementation history](docs/history.md) and the [earlier simplification plan](docs/sdd/plan-portfolio-simplification.md).
