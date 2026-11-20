# Deployment reference

The Telegram demo uses PostgreSQL for accepted updates and delivery state, Redis for conversation checkpoints, and RabbitMQ to wake workers. Only turn workers load Gemma and Laya. The hosted Compose file is deprecated and retains the PostgreSQL worker commands for rollback. [Local demo](../README.md#try-the-telegram-voice-demo) · [Telegram sequence](telegram-worker.md#full-call-sequence)

## Hosted configuration

Deploy the same tested application image for each role. Run `alembic upgrade head` on a fresh database before the API or workers start. Migration `0007_massage_studio` refuses a database containing existing business records. Set `DATABASE_URL`, `CHAT_REDIS_URL`, `GEMMA_BASE_URL` and `LAYA_BASE_URL` to trusted endpoints as required by each role. Budget PostgreSQL connections across replicas. `CAPY_DB_POOL_MAX=0` opens a connection per operation. A positive value caps each process’s SQLAlchemy pool with no overflow. Bot registration uses a separate connection until it releases its advisory lock.

| Role | Command | Configuration |
| --- | --- | --- |
| Webhook API | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | Serve HTTPS through a proxy. Give each bot an opaque route and its own webhook secret. Omit webhook bodies and secret headers from proxy logs. |
| Relay and maintenance | `python -m app.telegram_service relay` | Set `TELEGRAM_AMQP_URL` and `TELEGRAM_PARTITIONS`. This process also recovers expired sends and prunes old terminal rows. |
| Turn worker | `python -m app.telegram_service process --partition 0` | Use the same broker settings and one active worker per numbered partition. Supply the bot tokens, PostgreSQL, Redis and model settings. Leave enough shutdown time for long audio processing. |
| Sender | `python -m app.telegram_service send` | Use the same broker settings and bot tokens. Scale sender replicas independently. |

Configure the broker URL from the deployment secret store. Use a private virtual host for the CAPY queues. The demo uses one broker with a persistent volume. For hosted availability, provision replicated RabbitMQ quorum queues. [RabbitMQ quorum queues](https://www.rabbitmq.com/docs/quorum-queues)

| Endpoint | Response |
| --- | --- |
| `GET /live` | `200` when the API process responds. |
| `GET /health` | `200` when PostgreSQL and Redis respond, or `503` when either is unavailable. |
| `POST /telegram/<route>` | `204` after accepting or deduplicating a receipt. `403` rejects an invalid route or secret. `400` rejects an invalid update. `413` rejects a body over 256 KiB. `503` signals a database error. |

The webhook can accept an update while RabbitMQ is unavailable because the receipt and publication commit in PostgreSQL. Monitor oldest due publication age separately from API readiness. [Webhook handler](../app/main.py#L78)

## Register a test bot

After upgrading the schema and provisioning the business catalog, register with a dedicated test token and a random URL-safe webhook secret of at least 16 characters.

```sh
python -m app.telegram_service register \
  --business YOUR_BUSINESS_ID \
  --token-env YOUR_BOT_TOKEN_VAR --route YOUR_OPAQUE_ROUTE \
  --webhook-secret-env YOUR_BOT_WEBHOOK_SECRET_VAR \
  --webhook-base-url https://bot.example.com
```

Registration verifies the bot ID, copies eligible historical ready replies and installs the webhook with pending Telegram updates preserved. Test a text turn and a voice turn with a dedicated test chat. [Telegram webhook API](https://core.telegram.org/bots/api#setwebhook)

## Cutover and recovery

Create and seed a separate empty database for the massage studio. Keep an existing database with its earlier application for rollback. Stop any previous worker using the test bot before registering its webhook for the new demo.

If the broker loses its durable data, stop publishing and consuming, recreate its topology and backfill from PostgreSQL. Inspect `capy.telegram.v1.dead` when worker logs report a rejection. Fix the handler or configuration first, then rebuild wake-ups from unfinished database records. Discard malformed envelopes and investigate ambiguous sends before any manual action.

## Capacity checks

- The demo runs one incoming partition. Choose a partition count before release and set it on every relay and consumer. Start one consumer process for every index from zero through count minus one. Extra consumers for the same partition provide failover. Change the partition count only after pausing and draining the old layout. A long audio turn can delay other chats mapped to its queue.
- The default business limit is four active turns across partitions. Sender replicas share bot and chat rate reservations, initially 20 sends per second per bot and one send per second per chat. Telegram responses can require a lower rate. Outgoing jobs preserve chat order and broker readiness.
- Run `python -m app.telegram_service stats` to see ready-turn age, oldest due publication, outbound backlog and database lock waiters. At the broker, monitor ready and unacknowledged deliveries per queue, redeliveries and dead-letter depth. Correlate those metrics with turn and send elapsed times in the app logs. [Debug walkthrough](telegram-worker.md#follow-a-message-in-the-logs)

## Local verification

The benchmark uses a migrated disposable PostgreSQL database and a disposable RabbitMQ virtual host, both ending in `_test`. It runs real receipt, publication, turn and send transactions with stub inference and Telegram. Use a fresh database and drained test queues for each run.

```sh
PYTHONPATH=. uv run --locked python scripts/benchmark_telegram_queue.py \
  --transport rabbitmq --rate 20 --seconds 5 --idle-chats 100000
```

The local comparison used four turn workers, two senders, two bots and the same 30 ms text and 120 ms audio stubs. The 20 updates per second run included 100,000 idle chats. These timings measure the queue path in a disposable stack.

| Transport and load | Completed | p95 receipt to stub send |
| --- | --- | --- |
| PostgreSQL baseline at 20/s for 5 s | 100 | 0.96 s |
| RabbitMQ at 20/s for 5 s | 100 | 1.42 s |
| RabbitMQ at 100/s for 2 s with 500 ms audio stubs and no idle chats | 200 | 10.32 s |

See the [integration suite](../tests/integration/test_telegram_broker.py) for ordered text after voice, cross-partition progress, duplicates, unroutable publication and stale attempt recovery. Hosted failover, live Telegram canary and sustained traffic with real inference are release checks.
