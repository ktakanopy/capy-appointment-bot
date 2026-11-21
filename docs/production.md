# Deployment

The deployment consists of the API, one Telegram worker, PostgreSQL, Redis and the configured Gemma and Laya endpoints. The webhook accepts an update once it is saved in PostgreSQL. The worker polls the database for messages and replies.

## Runtime configuration

| Component | Settings |
| --- | --- |
| API and worker | Same `DATABASE_URL`, `CHAT_REDIS_URL`, `DEMO_BUSINESS_ID` and `TELEGRAM_BOT_ID`. The bot ID is the numeric prefix of the token. |
| API | `TELEGRAM_WEBHOOK_SECRET` and `TELEGRAM_WEBHOOK_ROUTE` (default `webhook`). Set `AGENDA_API_TOKEN` only if owner scheduling endpoints are needed. |
| Worker | `TELEGRAM_BOT_TOKEN` and the model endpoint settings. Give the process time to finish long audio turns during shutdown. |

Expose the webhook over HTTPS. Keep the private agenda routes behind TLS and use
a private owner token of at least 32 characters. The worker requires a direct or
session-pooled PostgreSQL connection to hold its advisory lock.

Apply `alembic upgrade head` before starting the API and worker. After both are
ready, register the webhook using the same route, secret and bot ID configured
for the API.

```sh
python -m app.telegram_service register --webhook-base-url https://bot.example.com
```

## Upgrade an existing deployment

For deployments already on `0009_telegram_postgres_queue`, stop the API and
worker, run `alembic upgrade head` to rename the inbox table, and start both
services with the updated application. To revert this rename, stop both services,
run `alembic downgrade 0009_telegram_postgres_queue`, and start the previous
application version.

For older deployments migrating off RabbitMQ:

1. Stop the old API, relay, worker and sender. Take a PostgreSQL backup and retain the deployed image and Redis checkpoint data.
2. Before migrating, read the old `telegram_bots` row to identify the bot ID, business and webhook route. Supply those values and the existing secrets in the new environment. If several bots have pending work, finish that work before switching to the single configured bot.
3. Apply the migrations. `0008_telegram_customers` preserves Telegram customer ownership. `0009_telegram_postgres_queue` keeps receipts, chat state and replies, copies pending send delays, and removes obsolete transport tables.
4. Start the new API and one worker. Re-register the webhook only if its address, route or secret has changed. Verify a real text or voice turn and that the appointment appears in the owner agenda.
5. After verification, retire the old RabbitMQ deployment. To revert, restore the database backup and run the matching earlier application.

`0007_massage_studio` requires a fresh business database for older product schemas. See the [schema guide](database.md#start-with-a-fresh-database).

## Monitor work

Check `/health` for PostgreSQL and Redis readiness. Check worker logs for repeated
database errors, ambiguous sends or growing delays. The worker can process one
turn at a time, so model latency affects queue wait times across chats.

```sql
SELECT min(ready_at) FROM telegram_inbox WHERE status = 'received';
SELECT min(ready_at) FROM telegram_outbox WHERE status = 'ready';
SELECT count(*) FROM telegram_outbox WHERE status = 'ambiguous';
```

An ambiguous send may already have reached the customer. Inspect it before any
manual resend. [Worker guide](telegram-worker.md#full-call-sequence)
