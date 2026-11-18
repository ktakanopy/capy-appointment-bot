# Deploy CAPY for multiple Telegram bots

CAPY uses one application image for webhook intake, text workers, audio workers, reminder schedulers, and senders. This runbook provides a single-host Compose reference and a staged cutover. Use a platform with replicated services and database failover for the design's availability target.

## Local development

The local `telegram` Compose profile receives Telegram updates through the API webhook.
Use a dedicated test bot and a public HTTPS reverse proxy or tunnel. The one-time
`telegram-register` service registers the URL with Telegram. Local reminders appear
in the owner CLI's demo inbox. [Local demo](../README.md#try-the-telegram-voice-demo)

## Hosted configuration

- Build a tested image and set `CAPY_IMAGE` to its tag or digest. Set `CAPY_ENV_FILE` to a private runtime environment file. Set `DATABASE_URL`, `CHAT_REDIS_URL`, `GEMMA_BASE_URL`, and `LAYA_BASE_URL` to reachable services. Set `CAPY_DB_POOL_MAX` to a small per-process maximum. Budget the sum across replicas, release jobs, and operators. A value of zero uses direct connections.
- Give each bot its own environment variables for its Bot API token and webhook secret. Set `CAPY_BOT_ENV_FILE` to the private token file and `CAPY_WEBHOOK_ENV_FILE` to the private webhook-secret file. The reference Compose config supplies them to different roles. Use a random URL-safe webhook secret of at least 16 characters. The registry stores environment variable names. Use an opaque URL-safe route ID for each bot. Provision businesses and their catalogs through a controlled data import or administrative process before registering a bot.
- Terminate HTTPS in front of the API and route `POST /telegram/<route>` to CAPY. Keep the models on private endpoints. Set proxy body limits for the webhook and omit request bodies and secret headers from proxy logs.

| Endpoint | Response |
| --- | --- |
| `GET /live` | `200` when the API process responds. |
| `GET /health` | `200` when PostgreSQL and Redis respond, or `503` when either is unavailable. |
| `POST /telegram/<route>` | `204` after committing the receipt or recognizing a duplicate. `403` rejects an invalid route or secret, `400` rejects an invalid update, `413` rejects a body over 256 KiB, and `503` signals a database error. |

The webhook's `204` acknowledges receipt. A separate worker handles the booking and queues the reply. [Webhook handler](../app/main.py#L78) · [Turn processing](architecture.md#how-the-telegram-worker-works)

## Release and register a test bot

Run the schema upgrade once before starting new processes. The production API command starts without demo seeding or migrations.

```sh
docker compose -f docker-compose.production.yml --profile release run --rm migrate
docker compose -f docker-compose.production.yml --profile release run --rm register \
  --business YOUR_BUSINESS_ID \
  --token-env YOUR_BOT_TOKEN_VAR --route YOUR_OPAQUE_ROUTE \
  --webhook-secret-env YOUR_BOT_WEBHOOK_SECRET_VAR
docker compose -f docker-compose.production.yml up -d
```

Registration verifies the bot identity with Telegram and refuses to remap historical records to a different business. Use `--deliver-reminders` when registering a test bot with real reminder delivery enabled. The app stores the trusted destination with bookings created through that bot. Historical demo deliveries stay in the owner inbox. A pending legacy reminder with no trusted route remains visible for operator review. Resolve legacy destinations explicitly before enabling those deliveries.

Configure the bot's webhook with `setWebhook`, its HTTPS URL, the matching `secret_token`, and `allowed_updates=["message"]`. Use your secret manager or deployment automation to pass credentials. Keep pending updates across the switch. Test text, voice, and a real reminder with controlled test data before enabling another business. [Telegram webhook API](https://core.telegram.org/bots/api#setwebhook)

## Cutover and recovery

1. Back up PostgreSQL and verify you can restore it into a disposable environment. Record ready, processing, and delivered counts and check for pending demo reminders. Stop the previous intake container, reply sender, and demo reminder worker before their replacements claim work. If upgrading from a release that used Telegram polling, stop its poller before webhook registration.
2. Apply the additive migration. Register the verified bot. Registration copies eligible legacy private-chat replies once and retains their delivery attempts and retry times. Reconcile pending and interrupted receipts, queued replies, and reminder routes before enabling delivery. Start with one test bot. Verify the first bot before adding a second business.
3. To roll back webhook intake, restore the previous compatible webhook deployment, then register its HTTPS origin for the same bot and route. Preserve pending Telegram updates and the database receipts so retransmitted updates can be deduplicated.
4. For a sender rollback, stop new claims and drain or reconcile in-flight jobs before switching paths. A dispatch that expired or lost its HTTP response stays `ambiguous`. Investigate before re-sending it. Automatic replay of an uncertain send could create a duplicate. Keep the additive schema during the rollback window and use compatible binaries. Full downgrade requires a verified backup and a reconciliation plan for new writes.

## Capacity checks

- Scale text and audio independently with `docker compose -f docker-compose.production.yml up -d --scale text=2 --scale audio=2 --scale sender=2` or the equivalent in your orchestrator. The starting per-business limit is four active turns. Measure queue age, model concurrency and database connections before raising it. Sender replicas share a starting budget of 20 sends per second per bot and one send per second per chat. The actual allowed throughput depends on Telegram responses.
- Run `python -m app.telegram_service stats` in the runtime environment to see ready-turn age, due-reminder age, outbound backlog per bot, and database connections and lock waiters.
- Set `CAPY_LOG_LEVEL=DEBUG` in the runtime environment to inspect decisions during a test. The default is `INFO`. [Debug log walkthrough](architecture.md#follow-one-booking-in-the-debug-logs)
- Collect intake, turn, model, booking, and outbound timing events. Measure p95 text receipt-to-acceptance, voice latency by duration, and reminder lateness separately. Measure PostgreSQL lock waits, backlog, sender 429s, and the size of Redis checkpoints. A send marked ambiguous or a reply that reports an unknown booking outcome is a failed customer journey for availability reporting.
- Run a sustained test with a Telegram stub at 1, 5, and 20 incoming turns per second. Include mixed bots, one busy business, slow voice requests, simultaneous due reminders, and worker restarts. Use controlled live tests to measure the Telegram delivery limit. Repeat the backup, failover, and reverse-cutover checks before expanding the canary. The 100,000-user and five-second p95 targets require these measurements.
- Consider RabbitMQ after measuring the PostgreSQL delivery path. If a broker is justified, PostgreSQL will still own reminder schedules and the authoritative outbox. Adopt a relay only after verifying publisher confirms, redelivery handling, and rollback against the same rate budgets. [Implementation plan](sdd/plan-multi-bot-production.md)

## Local verification

The queue benchmark accepts synthetic updates from two bots. It simulates model work and Telegram delivery while exercising the real PostgreSQL claims, rate reservations, and outbox. Run it with a fresh, migrated disposable database whose name ends in `_test`.

```sh
uv run python scripts/benchmark_telegram_queue.py --rate 20 --seconds 20 --idle-chats 100000
```

| Offered turns per second | Completed | p95 receipt to stub delivery |
| --- | --- | --- |
| 1 | 6 | 0.44 s |
| 5 | 30 | 0.46 s |
| 20, with 100,000 idle chats | 400 | 0.44 s |
| 100 burst | 200 | 7.17 s |

The two real-model voice fixtures and all 16 deterministic text fixtures passed. The real-model text suite passed nine of 16. The same seven cases failed at the pre-implementation commit using the same running model endpoints. Resolve those model-quality failures before live rollout. Hosted failover, a live Telegram canary, and a sustained run with real inference are pending.
