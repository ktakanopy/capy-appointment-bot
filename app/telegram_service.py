"""Run Telegram turn, reminder, delivery, and registration commands."""

from __future__ import annotations

import argparse
import os
import re
import signal
import time
import uuid
from dataclasses import replace
from threading import Event, Thread
from urllib.parse import urlsplit

import psycopg
from redis.exceptions import RedisError

from app.booking import BookingService
from app.channels.bots import BotRegistry, TelegramBot
from app.channels.telegram import TelegramBotApi
from app.channels.telegram_delivery import TelegramDelivery, TelegramOutbox, send_next
from app.channels.telegram_intake import private_message
from app.channels.telegram_repository import TelegramRepository
from app.channels.telegram_turns import (
    LEASE_SECONDS, REFRESH_OFFER, UNKNOWN_OUTCOME, ClaimLost, TelegramTurns, TurnClaim,
)
from app.config import load_settings
from app.database import connect
from app.observability import debug_event, debug_turn, get_logger, log_event
from app.reminders import enqueue_telegram_reminders
from app.runtime import close_runtime, create_runtime
from app.telegram_worker import TelegramWorker, _conversation_id


def reconcile_replies(database_url: str, bot: TelegramBot) -> None:
    """Copy legacy ready replies once. The caller holds the legacy worker lock."""
    with connect(database_url) as connection:
        connection.execute(
            """WITH queued AS (
                   INSERT INTO telegram_outbox
                       (business_id,bot_id,chat_id,update_id,message,reply_markup,attempts,ready_at)
                   SELECT business_id,bot_id,chat_id,update_id,reply_text,reply_markup,
                          greatest(attempts-1,0),next_attempt_at
                   FROM telegram_updates WHERE bot_id=%s AND business_id=%s
                     AND status='reply_ready' AND chat_id>0 AND chat_id=telegram_user_id
                     AND reply_text IS NOT NULL
                   ORDER BY received_at, update_id
                   ON CONFLICT (bot_id,update_id) DO NOTHING
               )
               UPDATE telegram_updates SET status='delivered', payload=NULL,
                   reply_text=NULL,reply_markup=NULL,expires_at=now()+interval '30 days'
               WHERE bot_id=%s AND business_id=%s AND status='reply_ready'""",
            (bot.bot_id, bot.business_id, bot.bot_id, bot.business_id),
        )


def process_turn(turns: TelegramTurns, claim: TurnClaim, runtime) -> None:
    with debug_turn(claim.bot.bot_id, claim.row["update_id"]):
        debug_event("telegram.turn.claimed", attempt=claim.generation)
        _process_turn(turns, claim, runtime)


def _process_turn(turns: TelegramTurns, claim: TurnClaim, runtime) -> None:
    started = time.monotonic()
    message = private_message(claim.row)
    if message is None:
        debug_event("telegram.turn.skipped", reason="unsupported_message")
        turns.finish(claim, None, claim.checkpoint_thread)
        return
    if claim.interrupted:
        debug_event("telegram.turn.skipped", reason="interrupted_attempt")
        turns.finish(claim, UNKNOWN_OUTCOME, None)
        return
    if claim.late:
        debug_event("telegram.turn.skipped", reason="out_of_order")
        turns.finish(claim, REFRESH_OFFER, claim.checkpoint_thread)
        return
    stop_heartbeat = Event()

    def heartbeat():
        while not stop_heartbeat.wait(LEASE_SECONDS / 3):
            try:
                if not turns.renew(claim):
                    return
            except psycopg.Error:
                # Booking and reply transactions still verify the lease before writing.
                return

    renewal = Thread(target=heartbeat, daemon=True)
    renewal.start()
    try:
        scoped_runtime = replace(
            runtime, bookings=BookingService(
                runtime.repository, turn_guard=claim.assert_owned,
                telegram_destination=(claim.bot.bot_id, claim.row["chat_id"]),
            ),
        )
        repository = TelegramRepository(turns.database_url, turn_guard=claim.assert_owned)
        try:
            api = claim.bot.client()
        except RuntimeError:
            turns.defer_before_execution(claim, "bot_credentials")
            log_event(runtime.logger, "telegram.turn.deferred", bot_id=claim.bot.bot_id,
                      failure_type="bot_credentials")
            return
        handler = TelegramWorker(scoped_runtime, api, repository)
        handler.bot_id = claim.bot.bot_id
        handler.business_id = claim.bot.business_id
        conversation_id = _conversation_id(claim.bot.business_id, claim.bot.bot_id, claim.row["chat_id"])
        handler.checkpoint_thread = f"{conversation_id}:attempt:{claim.generation}:{uuid.uuid4().hex}"
        try:
            if claim.checkpoint_thread and not runtime.workflow.graph.checkpointer.fork_completed(
                    claim.checkpoint_thread, handler.checkpoint_thread
            ):
                turns.finish(claim, "Your previous offer expired. Please ask for available times again.", None)
                return
        except RedisError:
            turns.defer_before_execution(claim, "checkpoint_unavailable")
            log_event(runtime.logger, "telegram.turn.deferred", bot_id=claim.bot.bot_id,
                      failure_type="checkpoint_unavailable")
            return
        identity = handler._ensure_customer(message["from"], claim.row["telegram_user_id"])
        debug_event("telegram.turn.customer_resolved")
        reply = handler._reply_to_message(claim.row, identity, message)
        checkpoint_to_publish = (
            handler.checkpoint_thread if handler.checkpoint_completed else claim.checkpoint_thread
        )
        turns.finish(claim, reply, checkpoint_to_publish)
        log_event(runtime.logger, "telegram.turn.finished", bot_id=claim.bot.bot_id,
                  queue_ms=round((time.time() - claim.row["received_at"].timestamp()) * 1000),
                  processing_ms=round((time.monotonic() - started) * 1000))
    finally:
        stop_heartbeat.set()
        renewal.join(timeout=1)


def run_worker(settings, args, stop: Event, logger) -> None:
    turns = TelegramTurns(settings.database_url)
    outbox = TelegramOutbox(settings.database_url)
    runtime = create_runtime(settings) if args.role == "process" else None
    delivery = TelegramDelivery()
    next_maintenance = 0.0
    next_prune = 0.0
    debug_event("telegram.worker.started", role=args.role, lane=args.lane)
    try:
        while not stop.is_set():
            did_work = False
            try:
                if args.role == "process":
                    claim = turns.claim(lane=args.lane, per_business=args.per_business)
                    if claim:
                        process_turn(turns, claim, runtime)
                        did_work = True
                elif args.role == "remind":
                    did_work = bool(enqueue_telegram_reminders(settings.database_url))
                else:
                    if time.monotonic() >= next_maintenance:
                        outbox.recover_expired()
                        next_maintenance = time.monotonic() + 5
                    if time.monotonic() >= next_prune:
                        prune_history(settings.database_url)
                        next_prune = time.monotonic() + 60
                    did_work = send_next(outbox, delivery, logger)
            except (psycopg.Error, ClaimLost) as error:
                log_event(logger, "telegram.worker.retry", role=args.role, failure_type=type(error).__name__)
                stop.wait(1)
            if not did_work:
                stop.wait(args.idle_seconds)
    finally:
        close_runtime(runtime)


def prune_history(database_url: str, limit: int = 500) -> None:
    """Delete old terminal records in bounded batches while retaining active jobs."""
    with connect(database_url) as connection:
        connection.execute(
            """DELETE FROM telegram_updates WHERE (bot_id,update_id) IN (
                   SELECT u.bot_id,u.update_id FROM telegram_updates u
                   WHERE u.status='delivered' AND u.expires_at<clock_timestamp()
                     AND NOT EXISTS (SELECT 1 FROM telegram_outbox o
                         WHERE o.bot_id=u.bot_id AND o.update_id=u.update_id
                           AND o.status IN ('ready','dispatching'))
                   ORDER BY u.expires_at LIMIT %s FOR UPDATE OF u SKIP LOCKED
               )""",
            (limit,),
        )
        connection.execute(
            """DELETE FROM telegram_outbox WHERE id IN (
                   SELECT o.id FROM telegram_outbox o
                   WHERE o.finished_at<clock_timestamp()-interval '30 days'
                     AND o.status IN ('sent','failed','canceled','ambiguous')
                   ORDER BY o.finished_at LIMIT %s FOR UPDATE OF o SKIP LOCKED
               )""",
            (limit,),
        )


def queue_stats(database_url: str, logger) -> None:
    """Show the age of ready work and the database connection budget."""
    with connect(database_url) as connection:
        ages = connection.execute(
            """SELECT
                (SELECT extract(epoch FROM clock_timestamp()-min(received_at))::float8
                 FROM telegram_updates WHERE status='received' AND ready_at<=clock_timestamp())
                    AS oldest_ready_turn_seconds,
                (SELECT extract(epoch FROM clock_timestamp()-min(send_at))::float8
                 FROM reminders WHERE status='pending' AND telegram_bot_id IS NOT NULL
                   AND send_at<=clock_timestamp()) AS oldest_due_reminder_seconds,
                (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database())
                    AS database_connections,
                (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
                   AND wait_event_type='Lock') AS database_lock_waiters"""
        ).fetchone()
        backlog = connection.execute(
            """SELECT bot_id,count(*) AS pending FROM telegram_outbox
               WHERE status IN ('ready','dispatching') GROUP BY bot_id ORDER BY bot_id"""
        ).fetchall()
    log_event(logger, "telegram.queue.snapshot", outbound_by_bot=backlog, **ages)


def register(settings, args) -> None:
    token = os.getenv(args.token_env)
    if not token or not args.business:
        raise ValueError("Supply --business and load the bot token environment variable.")
    webhook_payload = None
    if args.webhook_base_url is not None:
        base_url = urlsplit(args.webhook_base_url)
        if (
            base_url.scheme != "https"
            or not base_url.hostname
            or base_url.username is not None
            or base_url.path not in {"", "/"}
            or base_url.query
            or base_url.fragment
        ):
            raise ValueError("Use an HTTPS origin such as https://bot.example.com.")
        if not args.route or not args.webhook_secret_env:
            raise ValueError("Supply --route and --webhook-secret-env to configure a webhook.")
        secret = os.getenv(args.webhook_secret_env, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", secret):
            raise ValueError("Set a webhook secret of 16 to 256 URL-safe characters.")
        webhook_payload = {
            "url": f"https://{base_url.netloc}/telegram/{args.route}",
            "secret_token": secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": False,
        }
    api = TelegramBotApi(token)
    bot_id = api.get_me()["id"]
    repository = TelegramRepository(settings.database_url)
    lock = repository.acquire_worker_lock(bot_id)
    if lock is None:
        raise RuntimeError("Stop the legacy worker or poller for this bot before registering.")
    try:
        BotRegistry(settings.database_url).register(
            bot_id, args.business, args.token_env, webhook_route=args.route,
            webhook_secret_env=args.webhook_secret_env, reminder_delivery=args.deliver_reminders,
        )
        reconcile_replies(settings.database_url, TelegramBot(bot_id, args.business, args.token_env))
        if webhook_payload is not None:
            api.call("setWebhook", webhook_payload)
            log_event(get_logger(), "telegram.webhook.configured", bot_id=bot_id)
    finally:
        lock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("process", "send", "remind", "register", "stats"))
    parser.add_argument("--lane", choices=("all", "text", "audio"), default="all")
    parser.add_argument("--per-business", type=int, default=4)
    parser.add_argument("--idle-seconds", type=float, default=0.2)
    parser.add_argument("--business")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--route")
    parser.add_argument("--webhook-secret-env")
    parser.add_argument("--webhook-base-url", help="Public HTTPS origin for registering the webhook with Telegram.")
    parser.add_argument("--deliver-reminders", action="store_true")
    args = parser.parse_args()
    if not 0.01 <= args.idle_seconds <= 60 or not 1 <= args.per_business <= 100:
        parser.error("Use idle seconds between 0.01 and 60 and a per-business cap between 1 and 100.")
    settings = load_settings()
    if args.role == "register":
        register(settings, args)
        return
    logger = get_logger()
    if args.role == "stats":
        queue_stats(settings.database_url, logger)
        return
    stopping = Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopping.set())
    run_worker(settings, args, stopping, logger)


if __name__ == "__main__":
    main()
