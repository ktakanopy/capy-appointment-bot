from __future__ import annotations

import uuid

import psycopg

from app.database import connect, psycopg_url
from app.observability import debug_event


class TelegramRepository:
    """Keep customer identity links and coordinate bot registration."""

    def __init__(self, database_url: str, *, turn_guard=None):
        self.database_url = database_url
        self.turn_guard = turn_guard

    def acquire_worker_lock(self, bot_id: int):
        """Serialize registration and detect old workers during webhook cutover."""
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

    def ensure_customer_for_sender(
        self,
        business_id: str,
        bot_id: int,
        user_id: int,
        display_name: str,
        username: str | None = None,
    ) -> dict:
        """Resolve the sender and create a customer only when it has no customer link.

        The identity row is locked until creation and linking commit together.
        A phone number never selects an existing customer.
        """
        with connect(self.database_url) as connection:
            if self.turn_guard:
                self.turn_guard(connection)
            connection.execute(
                """INSERT INTO telegram_identities (business_id,bot_id,telegram_user_id,username,display_name)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (business_id,bot_id,telegram_user_id) DO UPDATE
                   SET username=EXCLUDED.username,display_name=EXCLUDED.display_name,updated_at=now()""",
                (business_id, bot_id, user_id, username, display_name),
            )
            identity = connection.execute(
                """SELECT * FROM telegram_identities
                   WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s FOR UPDATE""",
                (business_id, bot_id, user_id),
            ).fetchone()
            created_customer = identity["customer_id"] is None
            if created_customer:
                customer_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO customers (id,business_id,full_name,phone) VALUES (%s,%s,%s,%s)",
                    (
                        customer_id,
                        business_id,
                        identity["display_name"] or display_name,
                        identity["contact_phone"],
                    ),
                )
                connection.execute(
                    """UPDATE telegram_identities SET customer_id=%s,updated_at=now()
                       WHERE business_id=%s AND bot_id=%s AND telegram_user_id=%s""",
                    (customer_id, business_id, bot_id, user_id),
                )
                identity["customer_id"] = customer_id
        debug_event("telegram.identity.linked", created_customer=created_customer)
        return dict(identity)

    def save_contact(self, business_id: str, customer_id: str, phone: str) -> None:
        with connect(self.database_url) as connection:
            if self.turn_guard:
                self.turn_guard(connection)
            connection.execute(
                "UPDATE customers SET phone=%s WHERE business_id=%s AND id=%s",
                (phone, business_id, customer_id),
            )
        debug_event("telegram.contact.saved")
