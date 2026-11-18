"""Dispatch durable replies and reminders with shared Telegram rate budgets."""

from __future__ import annotations

import random
import time

from psycopg.errors import LockNotAvailable

from app.channels.bots import TelegramBot
from app.channels.telegram import TelegramApiError
from app.channels.telegram_publications import schedule_publication
from app.database import connect
from app.observability import debug_event, log_event

MAX_ATTEMPTS = 5
SEND_LEASE_SECONDS = 45
BOT_SEND_INTERVAL = 0.05
REPLY_BURST = 5
OUTGOING_DETAILS = """
    SELECT o.*, b.token_env, COALESCE(u.received_at,r.send_at,o.created_at) AS origin_at
    FROM telegram_outbox o
    JOIN telegram_bots b ON b.bot_id=o.bot_id AND b.business_id=o.business_id AND b.enabled
    LEFT JOIN telegram_updates u ON u.bot_id=o.bot_id AND u.update_id=o.update_id
    LEFT JOIN reminders r ON r.id=o.reminder_id AND r.generation=o.reminder_generation
"""
OLDER_SENDS = """
    SELECT 1 FROM telegram_outbox older
    WHERE older.bot_id=o.bot_id AND older.chat_id=o.chat_id
      AND older.id<o.id AND older.status IN ('ready','dispatching')
"""


class TelegramDelivery:
    def send(self, job: dict) -> None:
        bot = TelegramBot(job["bot_id"], job["business_id"], job["token_env"])
        bot.client().send_message(job["chat_id"], job["message"], job["reply_markup"])


class TelegramOutbox:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def claim(self) -> dict | None:
        """Authorize a ready dispatch and reserve both rate budgets atomically."""
        with connect(self.database_url) as connection:
            candidates = connection.execute(
                OUTGOING_DETAILS + f"""
                   LEFT JOIN telegram_bot_delivery bot ON bot.bot_id=o.bot_id
                   LEFT JOIN telegram_chat_delivery chat
                     ON chat.bot_id=o.bot_id AND chat.chat_id=o.chat_id
                   WHERE o.status='ready' AND o.ready_at<=clock_timestamp()
                     AND (bot.available_at IS NULL OR bot.available_at<=clock_timestamp())
                     AND (chat.available_at IS NULL OR chat.available_at<=clock_timestamp())
                     AND NOT EXISTS ({OLDER_SENDS})
                   ORDER BY CASE WHEN COALESCE(bot.reply_streak,0)>=%s
                                 THEN o.reminder_id IS NULL ELSE o.reminder_id IS NOT NULL END,
                            o.ready_at, o.id LIMIT 16""",
                (REPLY_BURST,),
            ).fetchall()
        for job in candidates:
            try:
                claimed = self._authorize(job)
            except LockNotAvailable:
                # Skip contended rows so other chats can still receive messages.
                continue
            if claimed:
                return claimed
        return None

    def claim_by_id(self, outgoing_id: int) -> dict | None:
        """Reserve the broker-selected send or persist a wake-up if delivery is blocked."""
        with connect(self.database_url) as connection:
            job = connection.execute(
                OUTGOING_DETAILS + " WHERE o.id=%s AND o.status='ready' AND o.ready_at<=clock_timestamp()",
                (outgoing_id,),
            ).fetchone()
        if job:
            try:
                claimed = self._authorize(job)
            except LockNotAvailable:
                claimed = None
            if claimed:
                return claimed
        self._wake_when_ready(outgoing_id)
        return None

    def _wake_when_ready(self, outgoing_id: int) -> None:
        """Keep blocked sends pending without holding a broker delivery or retrying the API."""
        with connect(self.database_url) as connection:
            row = connection.execute(
                f"""SELECT greatest(o.ready_at,bot.available_at,chat.available_at,
                          clock_timestamp()+CASE
                              WHEN NOT b.enabled THEN interval '1 minute'
                              WHEN EXISTS ({OLDER_SENDS}) THEN interval '0.25 seconds'
                              ELSE interval '0.05 seconds'
                          END) AS due_at
                   FROM telegram_outbox o JOIN telegram_bots b USING (bot_id,business_id)
                   LEFT JOIN telegram_bot_delivery bot ON bot.bot_id=o.bot_id
                   LEFT JOIN telegram_chat_delivery chat ON chat.bot_id=o.bot_id AND chat.chat_id=o.chat_id
                   WHERE o.id=%s AND o.status='ready'""",
                (outgoing_id,),
            ).fetchone()
            if row:
                schedule_publication(connection, outgoing_id=outgoing_id, due_at=row["due_at"])

    def _authorize(self, job: dict) -> dict | None:
        """Check current permissions, chat ordering and rate budgets before the HTTP call."""
        with connect(self.database_url) as connection:
            bot = connection.execute(
                """SELECT token_env FROM telegram_bots WHERE enabled AND bot_id=%s
                   AND business_id=%s FOR SHARE NOWAIT""",
                (job["bot_id"], job["business_id"]),
            ).fetchone()
            if not bot:
                return None
            if job["reminder_id"] and not self._lock_current_reminder(connection, job):
                connection.execute(
                    "SELECT id FROM reminders WHERE id=%s FOR UPDATE NOWAIT",
                    (job["reminder_id"],),
                )
                canceled = connection.execute(
                    """UPDATE telegram_outbox SET status='canceled', message=NULL,
                           finished_at=clock_timestamp(), last_error='stale_reminder'
                       WHERE id=%s AND status='ready'""",
                    (job["id"],),
                )
                if canceled.rowcount:
                    connection.execute(
                        """UPDATE reminders SET status='failed',last_error='invalid_delivery_route'
                           WHERE id=%s AND generation=%s AND status='pending'""",
                        (job["reminder_id"], job["reminder_generation"]),
                    )
                return None
            locked = connection.execute(
                f"""SELECT o.id FROM telegram_outbox o WHERE o.id=%s AND o.status='ready'
                   AND o.ready_at<=clock_timestamp() AND NOT EXISTS ({OLDER_SENDS})
                   FOR UPDATE SKIP LOCKED""",
                (job["id"],),
            ).fetchone()
            if not locked or not self._reserve(connection, job):
                return None
            attempt = connection.execute(
                """UPDATE telegram_outbox SET status='dispatching', attempts=attempts+1,
                       lease_until=clock_timestamp()+%s*interval '1 second'
                   WHERE id=%s RETURNING attempts, lease_until""",
                (SEND_LEASE_SECONDS, job["id"]),
            ).fetchone()
            return {**job, **bot, **attempt}

    @staticmethod
    def _lock_current_reminder(connection, job: dict) -> bool:
        # Match cancellation's booking, reminder, outbox lock order to avoid deadlocks.
        booking = connection.execute(
            """SELECT b.id, b.status, b.customer_id FROM reminders r
               JOIN bookings b ON b.id=r.booking_id AND b.business_id=r.business_id
               WHERE r.id=%s AND r.business_id=%s FOR UPDATE OF b NOWAIT""",
            (job["reminder_id"], job["business_id"]),
        ).fetchone()
        if not booking or booking["status"] == "canceled":
            return False
        return bool(connection.execute(
            """SELECT 1 FROM reminders r
               JOIN telegram_bots b ON b.bot_id=r.telegram_bot_id AND b.business_id=r.business_id
                   AND b.enabled AND b.reminder_delivery
               JOIN telegram_identities i ON i.business_id=r.business_id AND i.bot_id=b.bot_id
                   AND i.telegram_user_id=r.telegram_chat_id AND i.customer_id=%s
               WHERE r.id=%s AND r.status='pending' AND r.generation=%s
                   AND r.telegram_bot_id=%s AND r.telegram_chat_id=%s FOR UPDATE OF r NOWAIT""",
            (booking["customer_id"], job["reminder_id"], job["reminder_generation"],
             job["bot_id"], job["chat_id"]),
        ).fetchone())

    @staticmethod
    def _reserve(connection, job: dict) -> bool:
        bot_id, chat_id = job["bot_id"], job["chat_id"]
        connection.execute(
            "INSERT INTO telegram_bot_delivery (bot_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (bot_id,),
        )
        bot = connection.execute(
            """SELECT available_at<=clock_timestamp() AS eligible
               FROM telegram_bot_delivery WHERE bot_id=%s FOR UPDATE NOWAIT""",
            (bot_id,),
        ).fetchone()
        if not bot["eligible"]:
            return False
        chat = connection.execute(
            """INSERT INTO telegram_chat_delivery (bot_id, chat_id, available_at)
               VALUES (%s,%s,clock_timestamp()+interval '1 second')
               ON CONFLICT (bot_id,chat_id) DO UPDATE SET available_at=EXCLUDED.available_at
               WHERE telegram_chat_delivery.available_at<=clock_timestamp()
               RETURNING chat_id""",
            (bot_id, chat_id),
        ).fetchone()
        if not chat:
            return False
        connection.execute(
            """UPDATE telegram_bot_delivery
               SET available_at=clock_timestamp()+%s*interval '1 second',
                   reply_streak=CASE WHEN %s THEN 0 ELSE LEAST(reply_streak+1,%s) END
               WHERE bot_id=%s""",
            (BOT_SEND_INTERVAL, job["reminder_id"] is not None, REPLY_BURST, bot_id),
        )
        return True

    def finish(
        self, job: dict, status: str, *, error: str | None = None, retry_after: float = 0,
        bot_cooldown: int | None = None,
    ) -> bool:
        """Record the provider outcome and schedule any safe retry before acknowledgement."""
        with connect(self.database_url) as connection:
            # Lock the reminder first so completion and booking changes acquire locks in the same order.
            if job["reminder_id"]:
                connection.execute(
                    "SELECT id FROM reminders WHERE id=%s FOR UPDATE",
                    (job["reminder_id"],),
                )
            updated = connection.execute(
                """UPDATE telegram_outbox SET status=%s, last_error=%s, lease_until=NULL,
                       ready_at=clock_timestamp()+%s * interval '1 second',
                       finished_at=CASE WHEN %s='ready' THEN NULL ELSE clock_timestamp() END,
                       message=CASE WHEN %s='ready' THEN message END,
                       reply_markup=CASE WHEN %s='ready' THEN reply_markup END
                   WHERE id=%s AND attempts=%s AND status='dispatching'
                     AND lease_until>clock_timestamp() RETURNING ready_at""",
                (status, error, retry_after, status, status, status, job["id"], job["attempts"]),
            )
            updated_row = updated.fetchone()
            if not updated_row:
                return False
            if status == "ready":
                schedule_publication(connection, outgoing_id=job["id"], due_at=updated_row["ready_at"])
            if bot_cooldown:
                connection.execute(
                    """UPDATE telegram_bot_delivery SET available_at=GREATEST(
                           available_at,clock_timestamp()+%s * interval '1 second')
                       WHERE bot_id=%s""",
                    (bot_cooldown, job["bot_id"]),
                )
            if job["reminder_id"] and status != "ready":
                connection.execute(
                    """UPDATE reminders SET status=%s, attempts=%s, last_error=%s,
                           delivered_at=CASE WHEN %s='sent' THEN clock_timestamp() END
                       WHERE id=%s AND generation=%s AND status='pending'""",
                    ("delivered" if status == "sent" else "failed", job["attempts"], error,
                     status, job["reminder_id"], job["reminder_generation"]),
                )
        return True

    def recover_expired(self, limit: int = 50) -> int:
        """Expired sends are ambiguous. Automatic replay could message a user twice."""
        with connect(self.database_url) as connection:
            jobs = connection.execute(
                """SELECT id, reminder_id, reminder_generation, attempts FROM telegram_outbox
                   WHERE status='dispatching' AND lease_until<=clock_timestamp()
                   ORDER BY lease_until LIMIT %s""",
                (limit,),
            ).fetchall()
            recovered = 0
            for job in jobs:
                if job["reminder_id"]:
                    reminder = connection.execute(
                        """SELECT id FROM reminders WHERE id=%s FOR UPDATE SKIP LOCKED""",
                        (job["reminder_id"],),
                    ).fetchone()
                    if not reminder:
                        continue
                updated = connection.execute(
                    """UPDATE telegram_outbox SET status='ambiguous', message=NULL, reply_markup=NULL,
                           lease_until=NULL, finished_at=clock_timestamp(), last_error='dispatch_expired'
                       WHERE id=%s AND status='dispatching' AND lease_until<=clock_timestamp()""",
                    (job["id"],),
                )
                if updated.rowcount and job["reminder_id"]:
                    connection.execute(
                        """UPDATE reminders SET status='failed', attempts=%s, last_error='dispatch_expired'
                           WHERE id=%s AND generation=%s AND status='pending'""",
                        (job["attempts"], job["reminder_id"], job["reminder_generation"]),
                    )
                recovered += updated.rowcount
        return recovered


def send_next(outbox: TelegramOutbox, delivery: TelegramDelivery, logger) -> bool:
    """Drain one send using the PostgreSQL rollback worker."""
    job = outbox.claim()
    return deliver_claimed(outbox, delivery, logger, job)


def deliver_claimed(outbox: TelegramOutbox, delivery: TelegramDelivery, logger, job: dict | None) -> bool:
    """Call Telegram once for a reserved send and persist its classified outcome."""
    if job is None:
        return False
    started = time.monotonic()
    kind = "reminder" if job["reminder_id"] else "reply"
    debug_event("telegram.outbox.claimed", bot_id=job["bot_id"], update_id=job.get("update_id"),
                kind=kind, attempt=job["attempts"])
    try:
        delivery.send(job)
    except TelegramApiError as error:
        cooldown = error.retry_after if error.code in ("telegram_api_429", "telegram_http_429") else None
        if error.code == "telegram_transport_error":
            outcome = "ambiguous"
        elif error.is_permanent_rejection or job["attempts"] >= MAX_ATTEMPTS:
            outcome = "failed"
        else:
            outcome = "ready"
        delay = error.retry_after or min(2 ** job["attempts"], 60) + random.random()
        recorded = outbox.finish(job, outcome, error=error.code, retry_after=delay, bot_cooldown=cooldown)
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
              elapsed_since_origin_ms=round((time.time() - job["origin_at"].timestamp()) * 1000),
              duration_ms=round((time.monotonic() - started) * 1000))
    return True
