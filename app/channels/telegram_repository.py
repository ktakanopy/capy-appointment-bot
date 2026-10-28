from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.database import connect, psycopg_url


class TelegramRepository:
    """PostgreSQL identity links, polling offsets, and Telegram reply outbox."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def acquire_worker_lock(self, bot_id: int):
        connection = psycopg.connect(psycopg_url(self.database_url), autocommit=True)
        try:
            locked = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked",
                (f"telegram-worker:{bot_id}",),
            ).fetchone()[0]
        except Exception:
            connection.close()
            raise
        if not locked:
            connection.close()
            return None
        return connection

    def poll_offset(self, bot_id: int) -> int | None:
        with connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT next_update_id FROM telegram_poll_offsets WHERE bot_id=%s",
                (bot_id,),
            ).fetchone()
        return row["next_update_id"] if row else None

    def save_update(
        self,
        *,
        bot_id: int,
        update_id: int,
        business_id: str,
        chat_id: int | None,
        telegram_user_id: int | None,
        payload: dict,
    ) -> None:
        with connect(self.database_url) as connection:
            connection.execute(
                """INSERT INTO telegram_updates
                   (bot_id,update_id,business_id,chat_id,telegram_user_id,payload)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (bot_id,update_id) DO NOTHING""",
                (bot_id, update_id, business_id, chat_id, telegram_user_id, Jsonb(payload)),
            )
            connection.execute(
                """INSERT INTO telegram_poll_offsets (bot_id,next_update_id)
                   VALUES (%s,%s)
                   ON CONFLICT (bot_id) DO UPDATE SET
                       next_update_id=GREATEST(telegram_poll_offsets.next_update_id,EXCLUDED.next_update_id),
                       updated_at=now()""",
                (bot_id, update_id + 1),
            )

    def pending_updates(self, bot_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with connect(self.database_url) as connection:
            rows = connection.execute(
                """SELECT * FROM telegram_updates WHERE bot_id=%s AND status='received'
                   ORDER BY update_id LIMIT %s""",
                (bot_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_update(self, bot_id: int, update_id: int) -> dict[str, Any] | None:
        with connect(self.database_url) as connection:
            return connection.execute(
                """UPDATE telegram_updates SET status='processing',attempts=attempts+1
                   WHERE bot_id=%s AND update_id=%s AND status='received'
                   RETURNING *""",
                (bot_id, update_id),
            ).fetchone()

    def recover_interrupted(self, bot_id: int) -> None:
        # A crash may have happened after a booking commit but before its reply
        # was saved. Never rerun that graph turn; tell the user to check first.
        with connect(self.database_url) as connection:
            connection.execute(
                """UPDATE telegram_updates
                   SET status='reply_ready', payload=NULL,
                       reply_text=%s, reply_markup=NULL, last_error='worker_interrupted',
                       next_attempt_at=now()
                   WHERE bot_id=%s AND status='processing'""",
                (
                    "I couldn't confirm what happened to your previous request. "
                    "Please check your appointments before trying it again.",
                    bot_id,
                ),
            )

    def save_reply(
        self,
        bot_id: int,
        update_id: int,
        text: str | None,
        reply_markup: dict | None = None,
    ) -> None:
        with connect(self.database_url) as connection:
            connection.execute(
                """UPDATE telegram_updates
                   SET status='reply_ready',payload=NULL,reply_text=%s,reply_markup=%s,
                       next_attempt_at=now(),last_error=NULL
                   WHERE bot_id=%s AND update_id=%s AND status='processing'""",
                (text, Jsonb(reply_markup) if reply_markup is not None else None, bot_id, update_id),
            )

    def ready_replies(self, bot_id: int, limit: int = 25) -> list[dict[str, Any]]:
        with connect(self.database_url) as connection:
            rows = connection.execute(
                """SELECT u.bot_id,u.update_id,u.chat_id,u.reply_text,
                          u.reply_markup,u.attempts
                   FROM telegram_updates AS u
                   WHERE u.bot_id=%s AND u.status='reply_ready'
                     AND u.next_attempt_at <= now()
                     AND NOT EXISTS (
                         SELECT 1 FROM telegram_updates AS earlier
                         WHERE earlier.bot_id=u.bot_id
                           AND earlier.status='reply_ready'
                           AND (earlier.received_at,earlier.update_id)
                               < (u.received_at,u.update_id)
                     )
                   ORDER BY u.received_at,u.update_id LIMIT %s""",
                (bot_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_delivered(self, bot_id: int, update_id: int) -> None:
        with connect(self.database_url) as connection:
            connection.execute(
                """UPDATE telegram_updates SET status='delivered',reply_text=NULL,
                   reply_markup=NULL,last_error=NULL,expires_at=now()+interval '30 days'
                   WHERE bot_id=%s AND update_id=%s AND status='reply_ready'""",
                (bot_id, update_id),
            )

    def discard_reply(self, bot_id: int, update_id: int, code: str) -> None:
        """Clear a reply Telegram permanently rejected; retain its dedupe receipt."""
        with connect(self.database_url) as connection:
            connection.execute(
                """UPDATE telegram_updates SET status='delivered',reply_text=NULL,
                   reply_markup=NULL,last_error=%s,expires_at=now()+interval '30 days'
                   WHERE bot_id=%s AND update_id=%s AND status='reply_ready'""",
                (code[:64], bot_id, update_id),
            )

    def delay_reply(self, bot_id: int, update_id: int, delay_seconds: float, code: str) -> None:
        delay_seconds = min(max(delay_seconds, 1), 3600)
        with connect(self.database_url) as connection:
            connection.execute(
                """UPDATE telegram_updates SET attempts=attempts+1,
                   next_attempt_at=now()+(%s * interval '1 second'),last_error=%s
                   WHERE bot_id=%s AND update_id=%s AND status='reply_ready'""",
                (delay_seconds, code[:64], bot_id, update_id),
            )

    def save_username(self, business_id: str, bot_id: int, user_id: int, username: str | None) -> None:
        with connect(self.database_url) as connection:
            connection.execute(
                """INSERT INTO telegram_identities
                   (business_id,bot_id,telegram_user_id,username)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (business_id,bot_id,telegram_user_id) DO UPDATE
                   SET username=EXCLUDED.username,updated_at=now()""",
                (business_id, bot_id, user_id, username),
            )

    def identity(self, business_id: str, bot_id: int, user_id: int) -> dict[str, Any] | None:
        with connect(self.database_url) as connection:
            row = connection.execute(
                """SELECT * FROM telegram_identities
                   WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s""",
                (business_id, bot_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def save_verified_name(self, business_id: str, bot_id: int, user_id: int, name: str) -> None:
        with connect(self.database_url) as connection:
            connection.execute(
                """UPDATE telegram_identities SET verified_name=%s,updated_at=now()
                   WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s
                     AND verified_phone IS NOT NULL AND customer_id IS NULL""",
                (name, business_id, bot_id, user_id),
            )

    def customer(self, business_id: str, customer_id: str) -> dict[str, Any] | None:
        with connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT id,full_name,phone FROM customers WHERE business_id=%s AND id=%s",
                (business_id, customer_id),
            ).fetchone()
        return dict(row) if row else None

    def link_existing_customer(
        self, business_id: str, bot_id: int, user_id: int
    ) -> bool:
        """Finish a verified phone link when a previous booking turn was interrupted."""
        try:
            with connect(self.database_url) as connection:
                with connection.transaction():
                    identity = connection.execute(
                        """SELECT verified_phone,customer_id FROM telegram_identities
                           WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s
                           FOR UPDATE""",
                        (business_id, bot_id, user_id),
                    ).fetchone()
                    if identity is None or not identity["verified_phone"] or identity["customer_id"]:
                        return False
                    customer = connection.execute(
                        """SELECT id,full_name FROM customers
                           WHERE business_id=%s AND phone=%s""",
                        (business_id, identity["verified_phone"]),
                    ).fetchone()
                    if customer is None:
                        return False
                    linked = connection.execute(
                        """UPDATE telegram_identities
                           SET customer_id=%s,verified_name=%s,updated_at=now()
                           WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s
                             AND customer_id IS NULL
                           RETURNING customer_id""",
                        (customer["id"], customer["full_name"], business_id, bot_id, user_id),
                    ).fetchone()
                    return linked is not None
        except psycopg.errors.UniqueViolation:
            return False

    def verify_contact(
        self,
        business_id: str,
        bot_id: int,
        user_id: int,
        phone: str,
        name: str | None,
    ) -> str:
        """Save a verified number or link to its existing customer."""
        with connect(self.database_url) as connection:
            with connection.transaction():
                # Serialize claims for the same phone. The unique index is a
                # final guard if another writer bypasses this repository.
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"telegram-phone:{business_id}:{bot_id}:{phone}",),
                )
                identity = connection.execute(
                    """SELECT customer_id FROM telegram_identities
                       WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s
                       FOR UPDATE""",
                    (business_id, bot_id, user_id),
                ).fetchone()
                if identity is None:
                    return "identity_missing"
                claimed = connection.execute(
                    """SELECT 1 FROM telegram_identities
                       WHERE business_id=%s AND bot_id=%s AND verified_phone=%s
                         AND telegram_user_id<>%s""",
                    (business_id, bot_id, phone, user_id),
                ).fetchone()
                if claimed:
                    return "already_linked"
                customer = connection.execute(
                    "SELECT id FROM customers WHERE business_id=%s AND phone=%s",
                    (business_id, phone),
                ).fetchone()
                if identity["customer_id"] and (
                    customer is None or identity["customer_id"] != customer["id"]
                ):
                    return "already_linked"
                if customer:
                    existing_link = connection.execute(
                        """SELECT telegram_user_id FROM telegram_identities
                           WHERE business_id=%s AND bot_id=%s AND customer_id=%s
                             AND telegram_user_id<>%s""",
                        (business_id, bot_id, customer["id"], user_id),
                    ).fetchone()
                    if existing_link:
                        return "already_linked"
                    connection.execute(
                        """UPDATE telegram_identities SET customer_id=%s,verified_phone=%s,
                           verified_name=COALESCE(%s,verified_name),updated_at=now()
                           WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s""",
                        (customer["id"], phone, name, business_id, bot_id, user_id),
                    )
                    return "linked"
                connection.execute(
                    """UPDATE telegram_identities SET verified_phone=%s,
                       verified_name=COALESCE(%s,verified_name),updated_at=now()
                       WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s""",
                    (phone, name, business_id, bot_id, user_id),
                )
                return "verified"

    def attach_customer(
        self, business_id: str, bot_id: int, user_id: int, customer_id: str, phone: str, name: str,
    ) -> bool:
        try:
            with connect(self.database_url) as connection:
                row = connection.execute(
                    """UPDATE telegram_identities SET customer_id=%s,verified_phone=%s,
                           verified_name=%s,updated_at=now()
                       WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s
                         AND (customer_id IS NULL OR customer_id=%s)
                       RETURNING customer_id""",
                    (customer_id, phone, name, business_id, bot_id, user_id, customer_id),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            return False
        return row is not None

    def mark_ignored(self, bot_id: int, update_id: int) -> None:
        with connect(self.database_url) as connection:
            connection.execute(
                """UPDATE telegram_updates SET status='delivered',payload=NULL,
                   expires_at=now()+interval '30 days'
                   WHERE bot_id=%s AND update_id=%s AND status='processing'""",
                (bot_id, update_id),
            )

    def prune_receipts(self, bot_id: int) -> None:
        with connect(self.database_url) as connection:
            connection.execute(
                """DELETE FROM telegram_updates WHERE bot_id=%s AND status='delivered'
                   AND expires_at < now()""",
                (bot_id,),
            )
