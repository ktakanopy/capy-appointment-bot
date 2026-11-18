"""Measure CAPY queue overhead using synthetic updates and a stub sender."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import urlsplit

from app.channels.bots import BotRegistry, TelegramBot
from app.channels.telegram_delivery import TelegramOutbox
from app.channels.telegram_intake import save_update
from app.channels.telegram_turns import TelegramTurns
from app.database import connect
from app.seed import seed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=int, required=True)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--idle-chats", type=int, default=0)
    args = parser.parse_args()
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
    with connect(url) as connection:
        existing = connection.execute("SELECT 1 FROM telegram_bots LIMIT 1").fetchone()
        if existing:
            parser.error("Use a fresh test database without registered bots.")
    seed(include_second_business=True)
    bots = [
        TelegramBot(750_001, "demo-studio", "STUB_BOT_A"),
        TelegramBot(750_002, "second-demo-studio", "STUB_BOT_B"),
    ]
    registry = BotRegistry(url)
    for bot in bots:
        registry.register(bot.bot_id, bot.business_id, bot.token_env,
                          webhook_route=f"synthetic_webhook_{bot.bot_id}",
                          webhook_secret_env="STUB_WEBHOOK")
    if args.idle_chats:
        with connect(url) as connection:
            connection.execute(
                """INSERT INTO telegram_conversations (bot_id,chat_id,business_id)
                   SELECT %s,g,%s FROM generate_series(1,%s) AS g""",
                (bots[0].bot_id, bots[0].business_id, args.idle_chats),
            )
    stop = Event()
    turns, outbox = TelegramTurns(url), TelegramOutbox(url)

    def process(lane):
        while not stop.is_set():
            claim = turns.claim(lane=lane)
            if claim is None:
                stop.wait(0.01)
                continue
            time.sleep(0.12 if lane == "audio" else 0.03)
            turns.finish(claim, "Synthetic response", None)

    def send():
        durations = []
        while not stop.is_set():
            job = outbox.claim()
            if job is None:
                stop.wait(0.01)
                continue
            if outbox.finish(job, "sent"):
                durations.append(time.time() - job["origin_at"].timestamp())
        return durations

    total = args.rate * args.seconds
    with ThreadPoolExecutor(max_workers=6) as pool:
        processors = [pool.submit(process, lane) for lane in ("text", "text", "text", "audio")]
        senders = [pool.submit(send) for _ in range(2)]
        start = time.monotonic()
        try:
            for index in range(total):
                stop.wait(max(0, start + index / args.rate - time.monotonic()))
                bot = bots[1] if index % 5 == 0 else bots[0]
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
                for future in (*processors, *senders):
                    if future.done():
                        future.result()
                with connect(url) as connection:
                    sent = connection.execute(
                        "SELECT count(*) AS total FROM telegram_outbox WHERE status='sent'"
                    ).fetchone()["total"]
                if sent == total:
                    break
                stop.wait(0.1)
        finally:
            stop.set()
        durations = sorted(item for future in senders for item in future.result())
    if len(durations) != total:
        raise RuntimeError(f"Only {len(durations)} of {total} synthetic replies were sent.")
    print(json.dumps({
        "offered_turns_per_second": args.rate,
        "completed_turns": total,
        "idle_chats": args.idle_chats,
        "receipt_to_stub_delivery_p95_ms": round(durations[math.ceil(total * 0.95) - 1] * 1000),
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "uses_stub_models_and_telegram": True,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
