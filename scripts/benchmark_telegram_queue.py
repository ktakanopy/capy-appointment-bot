"""Compare processor counts with real queues and simulated inference and Telegram."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, get_ident
from unittest.mock import patch
from urllib.parse import urlsplit

import aio_pika
from sqlalchemy import text

from app.channels.bots import configured_bot
from app.channels.telegram import TelegramBotApi
from app.channels.telegram_intake import save_update
from app.channels.telegram_queue import QUEUE_NAME, TelegramQueue
from app.config import Settings
from app.database import database_session
from app.seed import seed
from app.telegram_sender import run_sender
from app.telegram_worker import _keep_turn_claim_alive, run_worker


def quantiles(values):
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "median_ms": None, "p95_ms": None}
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {"samples": len(ordered), "median_ms": round(median, 2),
            "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 2)}


async def benchmark(args, settings, processors):
    bot = configured_bot(settings)
    logger = logging.getLogger("capy.benchmark")
    logger.addHandler(logging.NullHandler())
    metrics = {"inbox_wait_ms": [], "processing_ms": [], "outbox_wait_ms": []}
    active = maximum = 0
    per_thread, per_thread_max = {}, {}
    mutex, stop = Lock(), Event()

    def simulate_turn(turns, claim, _runtime):
        nonlocal active, maximum
        thread = get_ident()
        started = time.monotonic()
        with mutex:
            active += 1
            maximum = max(maximum, active)
            per_thread[thread] = per_thread.get(thread, 0) + 1
            per_thread_max[thread] = max(per_thread_max.get(thread, 0), per_thread[thread])
            metrics["inbox_wait_ms"].append((time.time() - claim.row["received_at"].timestamp()) * 1000)
        try:
            with _keep_turn_claim_alive(turns, claim):
                time.sleep(args.processing_ms / 1000)
                turns.finish(claim, "Simulated reply", None)
        finally:
            with mutex:
                active -= 1
                per_thread[thread] -= 1
                metrics["processing_ms"].append((time.monotonic() - started) * 1000)

    def simulate_send(_api, _chat, _message, _markup):
        time.sleep(args.delivery_ms / 1000)

    import app.telegram_sender as sender
    original_delivery = sender.deliver_claimed

    def measured_delivery(outbox, logger, job):
        with mutex:
            metrics["outbox_wait_ms"].append((time.time() - job["created_at"].timestamp()) * 1000)
        original_delivery(outbox, logger, job)

    queue = TelegramQueue(settings.rabbitmq_url.get_secret_value(), bot.bot_id, logger)
    queue.start()
    started = time.monotonic()
    try:
        async with asyncio.timeout(5):
            while not queue.connected:
                await asyncio.sleep(0.02)
        with (patch("app.telegram_worker.process_turn", simulate_turn),
              patch("app.telegram_worker.create_runtime", lambda _: None),
              patch("app.telegram_worker.close_runtime", lambda _: None),
              patch.object(TelegramBotApi, "send_message", simulate_send),
              patch.object(sender, "deliver_claimed", measured_delivery),
              ThreadPoolExecutor(max_workers=processors + 1) as executor):
            jobs = [executor.submit(run_worker, settings, stop, logger) for _ in range(processors)]
            jobs.append(executor.submit(run_sender, settings, stop, logger))
            try:
                start_arrival = asyncio.get_running_loop().time()
                total = math.ceil(args.rate * args.seconds)
                for index in range(total):
                    await asyncio.sleep(max(0, start_arrival + index / args.rate - asyncio.get_running_loop().time()))
                    chat = 100_000 + (index % args.chats if args.distribution == "uniform" or index % 10 == 0 else 0)
                    item = {"update_id": index + 1, "message": {
                        "chat": {"id": chat, "type": "private"}, "from": {"id": chat},
                        "date": 1_000 + index, "text": "Synthetic turn",
                    }}
                    result = await asyncio.to_thread(save_update, settings.database_url, bot, item)
                    if not result.accepted:
                        raise RuntimeError("Synthetic update rejected")
                    await queue.try_publish(result.notification)
                deadline = time.monotonic() + args.drain_seconds
                while time.monotonic() < deadline:
                    for job in jobs:
                        if job.done():
                            job.result()
                    with database_session(settings.database_url) as session:
                        remaining = session.scalar(text("SELECT count(*) FROM telegram_outbox WHERE bot_id=:bot AND status='sent'"), {"bot": bot.bot_id})
                    if remaining == total:
                        break
                    await asyncio.sleep(0.05)
            finally:
                stop.set()
            await asyncio.gather(*(asyncio.to_thread(job.result, timeout=30) for job in jobs))
    finally:
        await queue.close()
    with database_session(settings.database_url) as session:
        params = {"bot": bot.bot_id}
        completed = session.scalar(text("SELECT count(*) FROM telegram_inbox WHERE bot_id=:bot AND status='processed'"), params)
        inbox = session.scalar(text("SELECT count(*) FROM telegram_inbox WHERE bot_id=:bot AND status<>'processed'"), params)
        outbox = session.scalar(text("SELECT count(*) FROM telegram_outbox WHERE bot_id=:bot AND status<>'sent'"), params)
    return {
        "processors": processors, "sender_count": 1, "sender_interval_seconds": sender.SEND_INTERVAL_SECONDS,
        "offered_turns": total, "completed_turns": completed, "active_turn_maximum": maximum,
        "per_processor_active_maximum": max(per_thread_max.values(), default=0),
        "residual_inbox": inbox, "residual_outbox": outbox,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        **{name: quantiles(values) for name, values in metrics.items()},
    }


async def clear_notifications(url):
    connection = await aio_pika.connect(url, timeout=3)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(QUEUE_NAME, durable=True)
        await queue.purge()


async def require_empty_broker(url):
    connection = await aio_pika.connect(url, timeout=3)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(QUEUE_NAME, durable=True)
        if queue.declaration_result.message_count or queue.declaration_result.consumer_count:
            raise RuntimeError("Use an empty dedicated test queue without active consumers")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processors", type=int, nargs="+", default=[1, 5])
    parser.add_argument("--rate", type=float, default=4)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--chats", type=int, default=20)
    parser.add_argument("--distribution", choices=["uniform", "hot"], default="uniform")
    parser.add_argument("--processing-ms", type=float, default=500)
    parser.add_argument("--delivery-ms", type=float, default=5)
    parser.add_argument("--drain-seconds", type=float, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (not 0 < args.rate <= 100 or not 0 < args.seconds <= 300 or not 1 <= args.chats <= 100_000
            or not 0 <= args.processing_ms <= 15_000 or not 0 <= args.delivery_ms <= 1_000
            or not 0 <= args.drain_seconds <= 120 or any(not 1 <= value <= 5 for value in args.processors)):
        parser.error("Use bounded rates, durations, latency, chats, and 1 to 5 processors")
    database_url = os.environ.get("TEST_DATABASE_URL", "")
    broker_url = os.environ.get("TEST_RABBITMQ_URL", "")
    if not all(urlsplit(url).path.endswith("_test") for url in (database_url, broker_url)):
        parser.error("Set TEST_DATABASE_URL and TEST_RABBITMQ_URL to disposable _test resources")
    os.environ.update(DATABASE_URL=database_url, TELEGRAM_BOT_TOKEN="750001:synthetic")
    settings = Settings(database_url=database_url, rabbitmq_url=broker_url, telegram_bot_id=750001,
                        tracing_enabled=False, telegram_recovery_interval_seconds=0.1)
    with database_session(database_url) as session:
        if session.scalar(text("SELECT EXISTS(SELECT 1 FROM telegram_inbox UNION ALL SELECT 1 FROM telegram_chat_processing_state UNION ALL SELECT 1 FROM telegram_outbox)")):
            parser.error("Use a migrated test database without existing Telegram work")
    seed()
    asyncio.run(require_empty_broker(broker_url))
    results = []
    for count in args.processors:
        try:
            results.append(asyncio.run(benchmark(args, settings, count)))
        finally:
            with database_session(database_url) as session:
                for table in ("telegram_outbox", "telegram_chat_processing_state", "telegram_inbox"):
                    session.execute(text(f"DELETE FROM {table} WHERE bot_id=750001"))
            asyncio.run(clear_notifications(broker_url))
    report = {"simulated_inference_and_telegram": True,
              "real_postgres_rabbitmq_and_role_entry_points": True,
              "configuration": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
              "runs": results}
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
