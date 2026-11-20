"""Measure CAPY queue overhead using synthetic updates and a stub sender."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import urlsplit

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert

from app.channels.bots import BotRegistry, TelegramBot
from app.channels.telegram_broker import INCOMING_QUEUE, OUTGOING_QUEUE, consume_wakeups, relay_publications
from app.channels.telegram_delivery import TelegramOutbox
from app.channels.telegram_intake import save_update
from app.channels.telegram_turns import TelegramTurns
from app.database import database_session
from app.db_models import TelegramBotRow, TelegramConversation, TelegramOutboxRow
from app.seed import seed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=int, required=True)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--idle-chats", type=int, default=0)
    parser.add_argument("--audio-ms", type=int, default=120)
    args = parser.parse_args()
    broker_url = os.getenv("TEST_AMQP_URL")
    if not broker_url or not urlsplit(broker_url).path.endswith("_test"):
        parser.error("Set TEST_AMQP_URL to a disposable broker vhost ending in _test.")
    if not 0 <= args.audio_ms <= 15_000:
        parser.error("Use an audio duration from 0 to 15000 ms.")
    url = os.environ["TEST_DATABASE_URL"]
    if (
        not urlsplit(url).path.endswith("_test")
        or not 1 <= args.rate <= 100
        or not 1 <= args.seconds <= 300
        or not 0 <= args.idle_chats <= 100_000
    ):
        parser.error("Use a fresh migrated _test database and bounded rate, duration and idle-chat counts.")
    os.environ["DATABASE_URL"] = url
    os.environ["DEMO_BUSINESS_ID"] = "demo-studio"
    with database_session(url) as session:
        if session.scalar(select(TelegramBotRow.bot_id).limit(1)) is not None:
            parser.error("Use a fresh test database without registered bots.")
    seed()
    bot = TelegramBot(750_001, "demo-studio", "STUB_BOT_A")
    registry = BotRegistry(url)
    registry.register(bot.bot_id, bot.business_id, bot.token_env,
                      webhook_route=f"synthetic_webhook_{bot.bot_id}",
                      webhook_secret_env="STUB_WEBHOOK")
    if args.idle_chats:
        chat_ids = func.generate_series(1, args.idle_chats).column_valued("chat_id")
        idle_chats = select(literal(bot.bot_id), chat_ids, literal(bot.business_id))
        with database_session(url) as session:
            session.execute(insert(TelegramConversation).from_select(
                ["bot_id", "chat_id", "business_id"], idle_chats,
            ))
    stop = Event()
    turns, outbox = TelegramTurns(url), TelegramOutbox(url)

    durations = []
    logger = logging.getLogger(__name__)

    def finish_turn(claim):
        audio = "voice" in claim.row["payload"]["message"]
        time.sleep(args.audio_ms / 1000 if audio else 0.03)
        turns.finish(claim, "Synthetic response", None)

    def finish_send(job):
        """Record a stubbed provider success using the real sender transaction."""
        if job and outbox.finish(job, "sent"):
            durations.append(time.time() - job["origin_at"].timestamp())

    def process_chat(bot_id, chat_id):
        """Exercise scoped turn selection through a real broker delivery."""
        claim = turns.claim_chat(bot_id, chat_id)
        if claim:
            finish_turn(claim)

    def send_outgoing(outgoing_id):
        """Exercise sends selected by real broker deliveries."""
        finish_send(outbox.claim_by_id(outgoing_id))

    total = args.rate * args.seconds
    with ThreadPoolExecutor(max_workers=3) as pool:
        workers = [
            pool.submit(relay_publications, broker_url, url, stop, logger, lambda: None),
            pool.submit(consume_wakeups, broker_url, INCOMING_QUEUE, 2, process_chat, stop, logger),
            pool.submit(consume_wakeups, broker_url, OUTGOING_QUEUE, 1, send_outgoing, stop, logger),
        ]
        start = time.monotonic()
        try:
            for index in range(total):
                stop.wait(max(0, start + index / args.rate - time.monotonic()))
                chat = 800_000 + index % 512
                message = {"chat": {"id": chat, "type": "private"}, "from": {"id": chat},
                           "date": int(time.time())}
                if index % 10 == 0:
                    message["voice"] = {"file_id": "stub"}
                else:
                    message["text"] = "Synthetic turn"
                if not save_update(url, bot, {"update_id": index + 1, "message": message}):
                    raise RuntimeError("Synthetic intake did not accept the update.")
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                for future in workers:
                    if future.done():
                        future.result()
                with database_session(url) as session:
                    sent = session.scalar(select(func.count()).select_from(TelegramOutboxRow).where(
                        TelegramOutboxRow.status == "sent",
                    ))
                if sent == total:
                    break
                stop.wait(0.1)
        finally:
            stop.set()
        for future in workers:
            future.result()
        durations.sort()
    if len(durations) != total:
        raise RuntimeError(f"Only {len(durations)} of {total} synthetic replies were sent.")
    print(json.dumps({
        "transport": "rabbitmq",
        "stub_audio_ms": args.audio_ms,
        "offered_turns_per_second": args.rate,
        "completed_turns": total,
        "idle_chats": args.idle_chats,
        "receipt_to_stub_delivery_p95_ms": round(durations[math.ceil(total * 0.95) - 1] * 1000),
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "uses_stub_models_and_telegram": True,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
