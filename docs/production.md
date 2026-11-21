# Deployment

Run the API, RabbitMQ, five initial Telegram processors and one independent sender, with PostgreSQL, Redis and the configured Gemma/Laya endpoints. PostgreSQL is authoritative for accepted work, chat claims and outgoing replies. See the [architecture atlas](codebase-atlas/index.html#architecture-overview) and [lost notification example](codebase-atlas/index.html#notification-recovery).

## Runtime configuration

| Role | Configuration |
| --- | --- |
| All application roles | Same `DATABASE_URL`, `DEMO_BUSINESS_ID`, `TELEGRAM_BOT_ID` |
| API | `CHAT_REDIS_URL`, `RABBITMQ_URL`, webhook route and secret; optional owner `AGENDA_API_TOKEN`. No bot token needed for intake/publication. |
| Processors | `CHAT_REDIS_URL`, `RABBITMQ_URL`, bot token, Gemma/Laya endpoints and audio limits |
| Sender | Bot token and database URL; no Redis, model or RabbitMQ dependency |

`RABBITMQ_URL` is secret configuration. Use separate vhosts/credentials for development, tests and production, and TLS for external connections. The queue `telegram.chat_ready` is durable and non-exclusive; notifications are persistent with publisher confirms and mandatory routing. Consumers use manual acknowledgments and prefetch one. There is no outgoing broker queue or relay.

`TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS` defaults to 1 (finite, positive, at most 5). It bounds the entire post-commit attempt, including waiting for a reusable broker channel and confirmation. `TELEGRAM_RECOVERY_INTERVAL_SECONDS` defaults to 1 (finite, positive, at most 60); instances add up to 10% jitter after each recovery opportunity finishes. Startup never waits for the broker. Reconnect attempts are bounded to one second, backing off from 0.25 to 5 seconds with jitter. An early notification can wait once, at most 0.5 seconds, without a database transaction or claim. Longer deferrals return to recovery.

Configure RabbitMQ's consumer acknowledgment timeout beyond the maximum supported turn and drain budget. The local/test [broker configuration](../config/rabbitmq.conf) uses 20 minutes, a 30-second heartbeat, and processors have 16 minutes of Compose shutdown grace. External production brokers must set a compatible timeout (20 minutes or longer). Network loss leaves the active database claim valid; its heartbeat continues. An old delivery is settled only on its original channel.

### Connection budget

The existing default `CAPY_DB_POOL_MAX=0` uses `NullPool`, retaining no idle application connections. Five processors can each use an operation connection plus a lease-heartbeat connection; the API has request concurrency and the sender uses an operation connection plus one dedicated lock session. Budget roughly 12 connections for processors/sender plus API request capacity and migration/operator headroom. Setting `CAPY_DB_POOL_MAX=2` caps each process's operation pool at two (seven roles: 14 pooled connections plus the sender lock), but does not bound concurrent API callers' waiting time. Measure before increasing pools. Each operation owns its SQLAlchemy session.

The sender's advisory lock requires a direct or session-pooled PostgreSQL connection, never transaction pooling. It checks that connection before each send and exits on connection loss. Stop the old runtime before switching lock names.

## Start the local deployment

Copy `.env.example` to a private `.env`, configure the bot and models, then run:

```sh
docker compose -p capy-massage-studio --profile telegram up -d --build --wait
docker compose -p capy-massage-studio ps telegram-worker telegram-sender
docker compose -p capy-massage-studio run --rm telegram-register \
  --webhook-base-url https://bot.example.com
```

Compose's `telegram-worker.deploy.replicas: 5` starts five processes by default; each runs `python -m app.telegram_service process` with one processing slot. `telegram-sender` runs `python -m app.telegram_service send` once. Override the worker count with `--scale telegram-worker=N`; this changes replicas, not concurrency inside each instance. RabbitMQ has persistent local storage and a health check, but neither API nor processor startup depends on broker health. Sender startup has no broker dependency.

For hosted deployment, use `docker-compose.production.yml` with `CAPY_IMAGE`, `CAPY_ENV_FILE`, `CAPY_BOT_ENV_FILE` and `CAPY_WEBHOOK_ENV_FILE`. The private runtime file supplies external PostgreSQL/Redis/RabbitMQ URLs; token and webhook files retain their separate scopes. Apply `alembic upgrade head` through the release `migrate` service, then `up -d api worker sender`. Production `worker` also defaults to five replicas. This is a reference deployment, with no claim of a highly available broker cluster.

## Changeover and rollback

1. Stop the previous combined worker before starting either new role. Do not run its sender alongside the new sender under different advisory lock names.
2. Retain the deployed image and database backup. Apply existing migrations as usual; this refactor adds no table or migration and preserves inbox, coordination state and outbox data.
3. Start the API, five processors and one sender. Keep the configured bot/business and webhook credentials. Verify durable intake, a completed turn and outbox delivery using a dedicated test chat before live rollout.
4. To roll back, stop both new roles before restoring the prior image and combined worker. Accepted work remains in the existing tables; do not purge it. No schema rollback is required for this refactor.

Older schema upgrades through `0007`/`0008`/`0009` have separate historical constraints. See [database migrations](database.md); this change does not restore the retired bot registry or broker tables. A live production rollout is separate from implementing and testing this change.

## Monitor work

`/live` reports process liveness. `/health` reports PostgreSQL, Redis and broker state. Broker loss yields `status=degraded` and `broker=unavailable` with HTTP 200 when PostgreSQL/Redis are ready, keeping durable intake and fallback enabled. Storage failure still returns 503. Without a configured bot, broker state is `disabled`.

Logs distinguish publication, connectivity, claim outcomes and work source (`notification` or `recovery`). `queue_ms` is measured when processing/delivery starts; processing duration and end-to-end elapsed time are separate. Rabbit depth counts hints, not unfinished inbox rows. Monitor database counts and age as well:

```sql
SELECT status, count(*), clock_timestamp() - min(received_at) AS oldest_age
FROM telegram_updates WHERE status IN ('received', 'processing') GROUP BY status;
SELECT status, count(*), clock_timestamp() - min(created_at) AS oldest_age
FROM telegram_outbox WHERE status IN ('ready', 'dispatching', 'ambiguous') GROUP BY status;
```

The sender retains roughly one send per second, persisted bot cooldown on 429, at most five attempts, bounded retry delays and conservative ambiguous outcomes for lost responses or expired dispatches. Inspect ambiguity before manual resend. Adding processors does not increase Telegram's send allowance.

## Local verification

The disposable Compose stack includes PostgreSQL, Redis and RabbitMQ in the `capy_test` vhost. It does not mount application data volumes.

```sh
docker compose -p capy-verification -f docker-compose.test.yml run --build --rm checks
docker compose -p capy-verification -f docker-compose.test.yml down
```

For the controlled benchmark, start only those services and set their host URLs:

```sh
docker compose -p capy-verification -f docker-compose.test.yml up -d --wait postgres redis rabbitmq
export TEST_DATABASE_URL=postgresql://scheduling:scheduling@localhost:15433/capy_simplification_test
export TEST_RABBITMQ_URL='amqp://capy_test:capy_test@localhost:15673/capy_test?heartbeat=2'
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
uv run python -m scripts.benchmark_telegram_queue --processors 1 5 \
  --rate 4 --seconds 3 --chats 20 --distribution uniform --processing-ms 500 \
  --delivery-ms 5 --drain-seconds 15 --output /tmp/capy-benchmark.json
```

Run alone against empty disposable Telegram tables and a dedicated empty broker vhost, with no other consumers. Inference and Telegram HTTP are simulated; intake, PostgreSQL claims/completion, RabbitMQ transport, sender locking/pacing and role entry points are real. Results include completed turns, total/per-instance active maximum, waiting/processing percentiles and residual inbox/outbox. Use identical arrivals for one/five runs. The [recorded evidence](verification/rabbitmq.md) describes the actual local checks; simulated results establish neither model capacity nor production throughput.
