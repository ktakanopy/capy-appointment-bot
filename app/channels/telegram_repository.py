from __future__ import annotations

import uuid

import psycopg
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert

from app.database import database_session, psycopg_url
from app.db_models import CustomerRow, TelegramIdentity
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
        with database_session(self.database_url) as session:
            if self.turn_guard:
                self.turn_guard(session)
            sender = insert(TelegramIdentity).values(
                business_id=business_id, bot_id=bot_id, telegram_user_id=user_id,
                username=username, display_name=display_name,
            )
            identity = session.scalars(sender.on_conflict_do_update(
                index_elements=["business_id", "bot_id", "telegram_user_id"],
                set_={
                    "username": sender.excluded.username,
                    "display_name": sender.excluded.display_name,
                    "updated_at": func.now(),
                },
            ).returning(TelegramIdentity)).one()
            created_customer = identity.customer_id is None
            if created_customer:
                customer = CustomerRow(
                    id=str(uuid.uuid4()),
                    business_id=business_id,
                    full_name=identity.display_name or display_name,
                    phone=identity.contact_phone,
                )
                session.add(customer)
                session.flush()
                identity.customer_id = customer.id
                identity.updated_at = func.now()
                session.flush()
                session.refresh(identity)
            result = {
                "customer_id": identity.customer_id,
                "telegram_user_id": identity.telegram_user_id,
                "display_name": identity.display_name,
            }
        debug_event("telegram.identity.linked", created_customer=created_customer)
        return result

    def save_contact(self, business_id: str, customer_id: str, phone: str) -> None:
        with database_session(self.database_url) as session:
            if self.turn_guard:
                self.turn_guard(session)
            session.execute(update(CustomerRow).where(
                CustomerRow.business_id == business_id, CustomerRow.id == customer_id,
            ).values(phone=phone))
        debug_event("telegram.contact.saved")
