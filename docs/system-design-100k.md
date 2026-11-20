# CAPY scale study: 100,000 monthly active customers

> **Status:** Planning exercise only. The traffic figures below are assumptions. The repository has not been tested with 100,000 monthly active customers, and no hosted availability or latency objective has been verified.

This document separates the current code path from a hypothetical workload estimate. It is not a deployment runbook. See the [production guide](production.md) for the current worker commands and the short, local queue benchmarks.

## Current runtime in the repository

The code currently receives Telegram updates through an authenticated webhook. For supported private messages, PostgreSQL records the update and its publication in one transaction; unsupported events receive a completed receipt without a worker notification. A relay publishes identifier-only wake-ups to RabbitMQ. Partitioned workers claim chats from PostgreSQL, run the conversation, and save replies to the PostgreSQL outbox. The relay and a separate sender deliver replies. The reminder scheduler writes due work to PostgreSQL; Telegram reminders use the same outbox when enabled, while the local demo can record reminders in the owner inbox.

```mermaid
flowchart LR
    TG[Telegram] --> API[FastAPI webhook]
    API -->|update receipt and publication| PG[(PostgreSQL)]
    PG -->|chat publication| Relay[Publication relay]
    Relay -->|bot and chat IDs| Incoming[RabbitMQ incoming partitions]
    Incoming --> Worker[Conversation worker]
    Worker <--> Redis[(Redis checkpoints)]
    Worker <--> Models[Gemma understanding and Laya routing]
    Worker -->|booking and reply outbox| PG
    Reminder[Reminder scheduler] -->|due reminder work| PG
    PG -->|outbox publication| Relay
    Relay -->|outgoing record ID| Outgoing[RabbitMQ outgoing queue]
    Outgoing --> Sender[Telegram sender]
    Sender --> TG
```

PostgreSQL owns booking and delivery state. RabbitMQ signals that work may be ready; it does not contain message bodies or replace the database records. Redis stores temporary conversation checkpoints. A database lease serializes turns for one chat. Independent chats can run in parallel when they use different partition workers and capacity is available. See the [architecture guide](architecture.md), [Telegram worker walkthrough](telegram-worker.md), and [idempotency and recovery notes](idempotency.md) for details.

The repository contains the runtime components, but that does not prove they are deployed as a highly available hosted service. The production guide is an operational reference, not evidence of a live 100k-customer deployment.

## Workload estimate

These values are an example to show how to size work. Replace them with measured customer behavior before choosing infrastructure.

| Input or result | Assumption |
| --- | ---: |
| Monthly active customers | 100,000 |
| Conversations per customer per month | 3 |
| Incoming messages per conversation | 8 |
| Incoming messages per month | 2,400,000 |
| Average incoming rate over 30 days | About 0.93 messages/second |
| Illustrative peak at 20 times the average | About 20 messages/second |
| Voice share and average duration | 10% at 15 seconds |
| Derived monthly audio | About 240,000 recordings or 1,000 audio hours |

At 20 incoming messages per second, a text turn that uses one Gemma request and one Laya request needs roughly 40 model requests per second before retries. Voice adds transcription work. Measure the actual requests, audio duration, tokens, and provider latency for the configured models.

For a mean end-to-end processing time of 3 seconds, Little's Law estimates about 60 turns in progress at 20 turns per second:

```text
in-progress turns ≈ arrival rate × mean processing time
                 ≈ 20 turns/second × 3 seconds
                 ≈ 60 turns
```

This is a workload estimate, not a worker count. Per-chat ordering, per-business limits, database capacity, model throughput, and Telegram delivery limits determine how many workers are useful.

## Constraints that shape scaling

- **Conversation order:** A voice turn can hold its chat until transcription and processing finish. More workers help other chats, but they cannot safely process later messages in the same chat at the same time.
- **Partitions:** RabbitMQ routes each chat to a partition. Add capacity only after measuring queue age and processing time. Change the partition count with the drain procedure in the [production guide](production.md#capacity-checks).
- **Model throughput:** Adding workers can make the inference queue longer if Gemma or Laya is already saturated. Measure both model wait time and execution time.
- **Database connections and locks:** Worker replicas share PostgreSQL. Budget each process's connection pool and watch lock waits on booking and claim transactions.
- **Telegram delivery:** More sender processes do not increase a bot's allowed send rate. Account for replies, reminders, retries, and rate-limit responses.
- **Audio:** Track recording duration and decoded memory, not only the number of queued recordings. Keep audio and text latency separate in reports.

## What the current evidence shows

The [production guide](production.md#local-verification) reports a local transport benchmark with stubbed inference and delivery. It ran short workloads at 20 messages per second and 100 messages per second. Those results help compare queue paths; they do not measure hosted Gemma/Laya throughput or establish 100k-customer capacity.

The [scenario evaluation framework](evaluation-framework.md) checks multi-turn behavior with isolated scheduling fixtures. Database and broker integration tests check persistence and recovery. These checks demonstrate behavior and correctness, not sustained production throughput.

The evidence in this repository does not establish a 100k monthly active user deployment, five-second response SLO, or 99.9% availability SLO. Those are possible targets from the original scale brief, not verified service commitments. Define the measurement window and use a measurable boundary such as webhook receipt to Telegram accepting the correct final reply; the bot cannot confirm that a customer saw it on their device. Measure voice separately by recording duration.

## Measurements needed before making a capacity claim

1. Measure actual conversation size, burst shape, audio share and duration, and replies per completed journey. Keep synthetic and production data separate.
2. Define the text and voice response targets, availability window, and what counts as a successful customer journey.
3. Run increasing and sustained loads against PostgreSQL, Redis, RabbitMQ, and representative Gemma/Laya endpoints. Include a hot chat, a hot business, slow audio, provider failures, Telegram rate limits, and broker or worker restarts.
4. Record end-to-end latency percentiles, queue age per partition, model wait and execution time, database connections and lock waits, delivery failures, retries, and operating cost.
5. Choose worker counts, partition count, pool sizes, and hosted redundancy from those results. Retest the failure and recovery behavior after each deployment change.

Do not add a deployment platform or autoscaling layer solely because the estimate says 100,000 monthly active customers. First identify the measured bottleneck and confirm that the application, model provider, and Telegram limits can support the target workload.

## Related guides

- [Architecture](architecture.md) — current components and booking flow
- [Production guide](production.md) — worker roles, recovery, and local benchmark
- [Model contracts](model-contracts.md) — Gemma and Laya inputs and outputs
- [Evaluation framework](evaluation-framework.md) — behavioral checks and reports
