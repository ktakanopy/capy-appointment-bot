"""Send saved replies through the Telegram Bot API."""

from __future__ import annotations

import random
import time
from threading import Event

import psycopg
from sqlalchemy import text

from app.channels.bots import configured_bot
from app.channels.telegram import TelegramApiError
from app.channels.telegram_delivery import TelegramOutbox
from app.database import DATABASE_ERRORS, database_session, psycopg_url
from app.observability import debug_event, log_event
from app.telegram_worker import _check_bot_scope

MAX_ATTEMPTS = 5
SEND_INTERVAL_SECONDS = 1


def acquire_sender_lock(database_url: str, bot_id: int):
    connection = psycopg.connect(
        psycopg_url(database_url), autocommit=True, connect_timeout=3,
        options="-c statement_timeout=3000", keepalives_idle=10, keepalives_interval=3,
        keepalives_count=3,
    )
    try:
        locked = connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
            (f"telegram-sender:{bot_id}",),
        ).fetchone()[0]
    except Exception:
        connection.close()
        raise
    if not locked:
        connection.close()
        return None
    return connection


def run_sender(settings, stop: Event, logger) -> None:
    bot = configured_bot(settings)
    if bot is None:
        raise RuntimeError("Set TELEGRAM_BOT_ID to start the sender.")
    bot.client()
    lock = acquire_sender_lock(settings.database_url, bot.bot_id)
    if lock is None:
        raise RuntimeError("A sender is already running for this bot.")
    with lock:
        _check_bot_scope(settings.database_url, bot)
        send_pending(TelegramOutbox(settings.database_url, bot), stop, logger, lock)


def send_pending(outbox: TelegramOutbox, stop: Event, logger, lock) -> None:
    next_send = next_recovery = next_prune = 0.0
    while not stop.is_set():
        # A lost session releases the singleton lock, so fail before another send.
        lock.execute("SELECT 1")
        try:
            now = time.monotonic()
            if now >= next_recovery:
                recovered = outbox.recover_expired()
                if recovered:
                    log_event(logger, "telegram.outbox.recovered", ambiguous=recovered)
                next_recovery = time.monotonic() + 5
            if now >= next_prune:
                prune_history(outbox.database_url)
                next_prune = time.monotonic() + 60
            reply = outbox.claim_next() if now >= next_send else None
            if reply is not None:
                try:
                    deliver_claimed(outbox, logger, reply)
                finally:
                    next_send = time.monotonic() + SEND_INTERVAL_SECONDS
            else:
                stop.wait(0.25)
        except DATABASE_ERRORS as error:
            log_event(logger, "telegram.sender.database_retry", failure_type=type(error).__name__)
            stop.wait(1)


def deliver_claimed(outbox: TelegramOutbox, logger, job: dict) -> None:
    """Call Telegram once for a reserved send and persist its classified outcome."""
    started = time.monotonic()
    waiting_ms = round((time.time() - job["created_at"].timestamp()) * 1000)
    kind = "reply"
    debug_event("telegram.outbox.claimed", bot_id=job["bot_id"], update_id=job.get("update_id"),
                kind=kind, attempt=job["attempts"])
    try:
        outbox.bot.client().send_message(job["chat_id"], job["message"], job["reply_markup"])
    except TelegramApiError as error:
        if error.code == "telegram_transport_error":
            outcome = "ambiguous"
        elif error.is_permanent_rejection or job["attempts"] >= MAX_ATTEMPTS:
            outcome = "failed"
        else:
            outcome = "ready"
        delay = error.retry_after or min(2 ** job["attempts"], 60) + random.random()
        recorded = outbox.finish(job, outcome, error=error.code, retry_after=delay)
    except RuntimeError:
        outcome = "failed"
        recorded = outbox.finish(job, outcome, error="delivery_configuration")
    else:
        outcome = "sent"
        recorded = outbox.finish(job, outcome)
    debug_event("telegram.outbox.result", bot_id=job["bot_id"], update_id=job.get("update_id"),
                kind=kind, outcome=outcome if recorded else "claim_lost")
    log_event(logger, "telegram.outbox.finished", bot_id=job["bot_id"],
              outcome=outcome if recorded else "claim_lost", provider_outcome=outcome,
              kind=kind,
              queue_ms=waiting_ms,
              retry_after_seconds=delay if outcome == "ready" else None,
              elapsed_since_origin_ms=round((time.time() - job["origin_at"].timestamp()) * 1000),
              duration_ms=round((time.monotonic() - started) * 1000))


def prune_history(database_url: str, limit: int = 500) -> None:
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
