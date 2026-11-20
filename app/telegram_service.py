"""Run Telegram turn, delivery, and registration commands."""

from __future__ import annotations

import argparse
import os
import re
import signal
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, Thread
from urllib.parse import urlsplit

from redis.exceptions import RedisError
from sqlalchemy import func, select, text

from app.booking import BookingService
from app.channels.bots import BotRegistry
from app.channels.telegram import TelegramBotApi
from app.channels.telegram_broker import INCOMING_QUEUE, OUTGOING_QUEUE, consume_wakeups, relay_publications
from app.channels.telegram_delivery import TelegramOutbox, deliver_claimed
from app.channels.telegram_intake import private_message
from app.channels.telegram_publications import backfill_publications
from app.channels.telegram_repository import TelegramRepository
from app.channels.telegram_turns import (
    LEASE_SECONDS, REFRESH_OFFER, UNKNOWN_OUTCOME, TelegramTurns, TurnClaim,
)
from app.config import load_settings
from app.database import DATABASE_ERRORS, database_session
from app.db_models import TelegramOutboxRow
from app.observability import debug_event, debug_turn, get_logger, log_event
from app.runtime import RuntimeContext, close_runtime, create_runtime
from app.telegram_worker import TelegramWorker, _conversation_id


def process_turn(turns: TelegramTurns, claim: TurnClaim, runtime) -> None:
    with debug_turn(claim.bot.bot_id, claim.row["update_id"]):
        debug_event("telegram.turn.claimed", attempt=claim.generation)
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

        with _keep_turn_claim_alive(turns, claim):
            worker = _prepare_turn_worker(turns, claim, runtime)
            if worker is None or not _restore_turn_checkpoint(turns, claim, worker):
                return
            reply, checkpoint = worker.handle_message(
                claim.row, message, claim.checkpoint_thread
            )
            turns.finish(claim, reply, checkpoint)
            log_event(
                runtime.logger, "telegram.turn.finished", bot_id=claim.bot.bot_id,
                queue_ms=round((time.time() - claim.row["received_at"].timestamp()) * 1000),
                processing_ms=round((time.monotonic() - started) * 1000),
            )


def _prepare_turn_worker(
    turns: TelegramTurns, claim: TurnClaim, runtime: RuntimeContext
) -> TelegramWorker | None:
    """Set up the worker that will handle one claimed Telegram message.

    A claim gives this attempt temporary ownership of the message. Reuse the
    shared workflow and model clients, but give this worker booking and
    customer services that verify ownership inside each write transaction.
    If the lease expires and another worker takes over, the old attempt
    cannot change a customer's account or booking.

    Give the attempt its own place in Redis to save the conversation draft.
    The caller can load the last completed draft into that space and publish
    its reference when the turn finishes and the claim is still valid.

    If the registered bot's credentials are unusable, schedule a retry and
    return None so the caller stops before processing the message.
    """
    scoped_runtime = replace(
        runtime,
        bookings=BookingService(
            runtime.repository,
            turn_guard=claim.assert_owned,
        ),
    )
    repository = TelegramRepository(turns.database_url, turn_guard=claim.assert_owned)
    try:
        api = claim.bot.client()
    except RuntimeError:
        _defer_turn(turns, claim, runtime, "bot_credentials")
        return None

    conversation_id = _conversation_id(claim.bot.business_id, claim.bot.bot_id, claim.row["chat_id"])
    # Give every attempt a unique Redis thread, even for retries of the same message.
    checkpoint = f"{conversation_id}:attempt:{claim.generation}:{uuid.uuid4().hex}"
    return TelegramWorker(scoped_runtime, api, repository, claim.bot, checkpoint)


def _restore_turn_checkpoint(turns: TelegramTurns, claim: TurnClaim, worker: TelegramWorker) -> bool:
    """Load the previous draft so a reply like '1' refers to the right offer.

    An expired draft requires a fresh offer. A Redis outage defers execution
    so the worker can try to recover the draft on its next attempt.
    """
    if not claim.checkpoint_thread:
        return True
    try:
        if worker.runtime.workflow.graph.checkpointer.fork_completed(
            claim.checkpoint_thread, worker.checkpoint_thread
        ):
            return True
        turns.finish(claim, "Your previous offer expired. Please ask for available times again.", None)
    except RedisError:
        _defer_turn(turns, claim, worker.runtime, "checkpoint_unavailable")
    return False


def _defer_turn(turns: TelegramTurns, claim: TurnClaim, runtime: RuntimeContext, reason: str) -> None:
    """Reschedule a setup failure before the turn has made any customer or booking changes."""
    turns.defer_before_execution(claim, reason)
    log_event(runtime.logger, "telegram.turn.deferred", bot_id=claim.bot.bot_id, failure_type=reason)


@contextmanager
def _keep_turn_claim_alive(turns: TelegramTurns, claim: TurnClaim) -> Iterator[None]:
    """Keep the claim valid during inference, which can outlast the lease.

    Renew in the background while the turn blocks on models. Stop renewal
    when the turn exits, including when processing raises an error.
    """
    stop_renewal = Event()
    renewal = Thread(target=_renew_turn_claim, args=(turns, claim, stop_renewal), daemon=True)
    renewal.start()
    try:
        yield
    finally:
        stop_renewal.set()
        renewal.join(timeout=1)


def _renew_turn_claim(turns: TelegramTurns, claim: TurnClaim, stop: Event) -> None:
    """Refresh the lease while ownership holds.

    If renewal fails, booking and reply transactions still verify the claim
    before writing.
    """
    while not stop.wait(LEASE_SECONDS / 3):
        try:
            if not turns.renew(claim):
                return
        except DATABASE_ERRORS:
            return


def _maintain_deliveries(database_url: str):
    """Run bounded expiry and retention sweeps independently of broker delivery order."""
    outbox = TelegramOutbox(database_url)
    next_recovery = next_prune = 0.0

    def maintain() -> None:
        """Sweep periodically so the relay can publish promptly between checks."""
        nonlocal next_recovery, next_prune
        now = time.monotonic()
        if now >= next_recovery:
            outbox.recover_expired()
            next_recovery = now + 5
        if now >= next_prune:
            prune_history(database_url)
            next_prune = now + 60

    return maintain


def run_broker_worker(settings, args, stop: Event, logger) -> None:
    """Run a relay, an incoming consumer, or an outgoing consumer."""
    database_url = settings.database_url
    broker_url = os.environ["TELEGRAM_AMQP_URL"]
    if args.role == "relay":
        relay_publications(broker_url, database_url, stop, logger,
                           _maintain_deliveries(database_url), args.publication_role)
        return
    if args.role == "send":
        outbox = TelegramOutbox(database_url)

        def send_outgoing(outgoing_id: int) -> None:
            """Claim by ID so RabbitMQ selects work and PostgreSQL enforces send policy."""
            deliver_claimed(outbox, logger, outbox.claim_by_id(outgoing_id))

        consume_wakeups(broker_url, OUTGOING_QUEUE, 1, send_outgoing, stop, logger)
        return
    turns = TelegramTurns(database_url)
    runtime = create_runtime(settings)

    def process_chat_turn(bot_id: int, chat_id: int) -> None:
        """Run the head turn for this chat or leave its deferred wake-up in PostgreSQL."""
        claim = turns.claim_chat(bot_id, chat_id)
        if claim:
            process_turn(turns, claim, runtime)

    try:
        consume_wakeups(broker_url, INCOMING_QUEUE, 2,
                        process_chat_turn, stop, logger)
    finally:
        close_runtime(runtime)


def prune_history(database_url: str, limit: int = 500) -> None:
    """Delete old terminal records in bounded batches while retaining active jobs."""
    with database_session(database_url) as session:
        session.execute(
            text("""DELETE FROM telegram_updates WHERE (bot_id,update_id) IN (
                   SELECT u.bot_id,u.update_id FROM telegram_updates u
                   WHERE u.status='delivered' AND u.expires_at<clock_timestamp()
                     AND NOT EXISTS (SELECT 1 FROM telegram_outbox o
                         WHERE o.bot_id=u.bot_id AND o.update_id=u.update_id
                           AND o.status IN ('ready','dispatching'))
                   ORDER BY u.expires_at LIMIT :limit FOR UPDATE OF u SKIP LOCKED
               )"""),
            {"limit": limit},
        )
        session.execute(
            text("""DELETE FROM telegram_outbox WHERE id IN (
                   SELECT o.id FROM telegram_outbox o
                   WHERE o.finished_at<clock_timestamp()-interval '30 days'
                     AND o.status IN ('sent','failed','canceled','ambiguous')
                   ORDER BY o.finished_at LIMIT :limit FOR UPDATE OF o SKIP LOCKED
               )"""),
            {"limit": limit},
        )


def queue_stats(database_url: str, logger) -> None:
    """Show the age of ready work and the database connection budget."""
    with database_session(database_url) as session:
        ages = session.execute(
            text("""SELECT
                (SELECT extract(epoch FROM clock_timestamp()-min(received_at))::float8
                 FROM telegram_updates WHERE status='received' AND ready_at<=clock_timestamp())
                    AS oldest_ready_turn_seconds,
                (SELECT extract(epoch FROM clock_timestamp()-min(due_at))::float8
                 FROM telegram_publications WHERE due_at<=clock_timestamp())
                    AS oldest_due_publication_seconds,
                (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database())
                    AS database_connections,
                (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
                   AND wait_event_type='Lock') AS database_lock_waiters""")
        ).mappings().one()
        backlog = session.execute(
            select(TelegramOutboxRow.bot_id, func.count().label("pending"))
            .where(TelegramOutboxRow.status.in_(("ready", "dispatching")))
            .group_by(TelegramOutboxRow.bot_id).order_by(TelegramOutboxRow.bot_id)
        ).mappings().all()
    log_event(logger, "telegram.queue.snapshot", outbound_by_bot=[dict(row) for row in backlog], **ages)


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
            webhook_secret_env=args.webhook_secret_env,
        )
        if webhook_payload is not None:
            api.call("setWebhook", webhook_payload)
            log_event(get_logger(), "telegram.webhook.configured", bot_id=bot_id)
    finally:
        lock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("process", "send", "relay", "backfill", "register", "stats"))
    parser.add_argument("--publication-role", choices=("process", "send"))
    parser.add_argument("--business")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--route")
    parser.add_argument("--webhook-secret-env")
    parser.add_argument("--webhook-base-url", help="Public HTTPS origin for registering the webhook with Telegram.")
    args = parser.parse_args()
    broker_role = args.role in ("relay", "process", "send")
    if broker_role and not os.environ.get("TELEGRAM_AMQP_URL"):
        parser.error("Set TELEGRAM_AMQP_URL to run a RabbitMQ worker.")
    settings = load_settings()
    if args.role == "register":
        register(settings, args)
        return
    logger = get_logger()
    if args.role == "stats":
        queue_stats(settings.database_url, logger)
        return
    if args.role == "backfill":
        incoming, outgoing = backfill_publications(settings.database_url, args.publication_role)
        log_event(logger, "telegram.publications.backfilled", incoming=incoming, outgoing=outgoing)
        return
    stopping = Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopping.set())
    run_broker_worker(settings, args, stopping, logger)


if __name__ == "__main__":
    main()
