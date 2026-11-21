# RabbitMQ runtime verification

Verified locally on November 21, 2026. This is implementation evidence, not a production deployment or throughput commitment. PostgreSQL remains the authoritative work record; no schema change or data migration was needed.

## Important changes

| File | Responsibility |
| --- | --- |
| `app/channels/telegram_queue.py` | One durable identifier queue, reusable async transport, bounded confirmed publication, manual settlement on the original delivery channel |
| `app/channels/telegram_intake.py`, `app/main.py` | Typed acceptance; publish after the intake session commits and closes; retain HTTP acceptance on broker failure |
| `app/channels/telegram_turns.py` | Explicit claimed/busy/not_ready/empty outcomes with database-derived readiness; existing generations and guards |
| `app/telegram_worker.py` | One dedicated execution thread per instance, shared by notifications and startup/periodic recovery, with safe cancellation draining |
| `app/telegram_sender.py` | Independent sender, per-bot session advisory lock, conservative pacing, expired-send recovery and bounded pruning |
| Compose and CI | Five initial processors, one sender, disposable RabbitMQ vhost, required integration configuration |
| Atlas inputs and generated HTML/SVGs | Current architecture overview, failure/recovery and independent-progress examples, refreshed source snapshots |

## Checks actually run

- Locked dependency sync: passed (`uv sync --locked --extra dev`), using a temporary writable uv cache.
- Host Python 3.13 full suite against real disposable PostgreSQL 17, Redis 7 and RabbitMQ 4.2: **162 passed** in 27.37 seconds.
- Python 3.11 Docker test image, same three service types and required integration mode: **162 passed** in 35.68 seconds.
- Latest scheduling, broker and process-signal tests mounted into the Python 3.11 image after final changes: **14 passed** in 13.09 seconds. The equivalent host focused run also passed.
- Offline deterministic bilingual task corpus: **16/16 passed**.
- Local Compose, local plus observability, disposable test Compose and production Compose with nonsecret placeholder paths: configuration validation passed.
- Actual isolated local Compose startup (5.6.1): observed **five** `telegram-worker` containers and **one** `telegram-sender`, plus API and required stores. Used synthetic credentials, no exposed host ports and isolated volumes; no live customer messages or model calls.
- Stopped that isolated broker and restarted API/processors/sender: all roles started; processor logs continued `source=recovery`. `/health` returned **HTTP 200**, `status=degraded`, healthy PostgreSQL/Redis and `broker=unavailable`. This checks that deployment readiness does not disable fallback.
- Atlas generator resolved every referenced Python symbol and regenerated HTML plus 17 SVGs. Chrome desktop and **390 × 844** checks covered the default overview, processor inspector/source reader, new types in search, existing `#walkthrough/selection-text` bookmark, transcript and Open SVG. Mobile navigation/search/inspector remained usable with pan/zoom for diagram detail. Temporary viewport override was reset.
- Static SVG and PNG preview inspected for text, connectors and source labels. `git diff --check` passed.

The host emitted the existing FastAPI TestClient deprecation warning; the Docker image emitted a LangGraph serialization deprecation warning. Neither caused a failure. The browser extension emitted an asynchronous-listener warning during tab handling; no atlas interaction failed as a result.

The integration scenarios include five independent chats with a serialized follow-up; independent sender progress; post-commit publication failure; unavailable-broker startup/recovery; early and one-minute-deferred hints; busy/empty/duplicate hints; fairness in both directions; reconnect and redelivery without duplicate completion; stale booking/customer/checkpoint ownership; booking committed before a crash; lease renewal while synchronous work blocks; cancellation draining; real SIGTERM/SIGKILL behavior; sender singleton/lock-connection loss; and cooldown/permanent/ambiguous delivery classifications.

## Reproduce

From the repository root:

```sh
uv sync --locked --extra dev
docker compose -p capy-verification -f docker-compose.test.yml run --build --rm checks
uv run python -m app.evals.runner --understanding deterministic --routing deterministic \
  --output .eval_runs/offline.json
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.observability.yml config --quiet
docker compose -f docker-compose.test.yml config --quiet
CAPY_IMAGE=capy:verify CAPY_ENV_FILE=.env.example CAPY_BOT_ENV_FILE=.env.example \
  CAPY_WEBHOOK_ENV_FILE=.env.example docker compose -f docker-compose.production.yml config --quiet
python3 skills/codebase-atlas/scripts/render_atlas.py --web --project CAPY \
  --out docs/codebase-atlas docs/codebase-atlas/flows.json docs/codebase-atlas/files.json
docker compose -p capy-verification -f docker-compose.test.yml down
```

Host tests additionally need the three `TEST_*_URL` settings described in the [production guide](../production.md#local-verification), FFmpeg and `CAPY_REQUIRE_INTEGRATION=1`. Test databases/vhosts must end in `_test`; missing broker configuration fails when integration checks are required.

## Controlled comparison

The [complete JSON receipt](rabbitmq-benchmark.json) comes from the actual `run_worker` and `run_sender` entry points. PostgreSQL intake/claims/completion, RabbitMQ, locking, scheduling and pacing were real. Inference was a **500 ms sleep**, and Telegram delivery a **5 ms sleep** with no HTTP. The initial readiness window stayed at 300 ms. Both runs offered 12 turns, at 4/second for 3 seconds, round-robin across a configured pool of 20 chats, followed by up to 15 seconds to drain. Recovery cadence was 100 ms for the controlled workload; deployment default is 1 second plus jitter.

| Measurement | One processor | Five processors |
| --- | ---: | ---: |
| Completed turns | 12 | 12 |
| Total active turn maximum | 1 | 3 |
| Per-instance active maximum | 1 | 1 |
| Inbox wait median / p95 | 2053 / 3798 ms | 334 / 340 ms |
| Processing median / p95 | 528 / 545 ms | 522 / 531 ms |
| Outbox wait median / p95 | 2819 / 5586 ms | 4736 / 9132 ms |
| Residual inbox / outbox | 0 / 0 | 0 / 0 |
| Total elapsed | 12.74 s | 12.91 s |

The five-processor workload reached three simultaneous turns because its offered rate and simulated latency did not occupy all five slots. The separate barrier-based integration check admitted all five. Earlier completion moves waiting into the paced outbox, so it does not imply faster total Telegram drain. With 12 samples, tail figures are descriptive and noisy. These results measure neither Gemma/Laya capacity, real Telegram throughput, 100,000-customer capacity nor availability objectives.

## Deliverables and scope

Open the [interactive atlas](../codebase-atlas/index.html), [source-linked SVG](../codebase-atlas/architecture-overview.svg) or [PNG preview](../codebase-atlas/architecture-overview.png). The runtime uses the existing physical table names `telegram_updates`, `telegram_conversations` and `telegram_outbox`, corresponding to the brief's conceptual inbox and processing-state names. The notification adapter uses explicit bounded reconnects with aio-pika rather than transparent delivery-handle restoration. Live Gemma/Laya load, live Telegram delivery and production rollout remain separate operations.

Client behavior was checked against the [aio-pika lifecycle/API documentation](https://docs.aio-pika.com/quick-start.html) and [RabbitMQ consumer acknowledgment timeout guidance](https://www.rabbitmq.com/docs/consumers#acknowledgement-timeout). Confirms and consumer acknowledgments remain separate from PostgreSQL transactions.
