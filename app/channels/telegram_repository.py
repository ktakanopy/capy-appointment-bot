from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.database import database_session
from app.db_models import CustomerRow
from app.observability import debug_event


class TelegramRepository:
    """Keep customer identity links under the active turn ownership guard."""

    def __init__(self, database_url: str, *, turn_guard=None):
        self.database_url = database_url
        self.turn_guard = turn_guard

    def ensure_customer_for_sender(
        self,
        business_id: str,
        bot_id: int,
        user_id: int,
        display_name: str,
        username: str | None = None,
    ) -> dict:
        """Keep the same customer ID when a verified sender's profile changes."""
        with database_session(self.database_url) as session:
            if self.turn_guard:
                self.turn_guard(session)
            sender = insert(CustomerRow).values(
                id=str(uuid.uuid4()),
                business_id=business_id,
                full_name=display_name,
                telegram_bot_id=bot_id,
                telegram_user_id=user_id,
                telegram_username=username,
                telegram_display_name=display_name,
            )
            customer = session.execute(sender.on_conflict_do_update(
                index_elements=["business_id", "telegram_bot_id", "telegram_user_id"],
                set_={
                    "telegram_username": sender.excluded.telegram_username,
                    "telegram_display_name": sender.excluded.telegram_display_name,
                },
            ).returning(
                CustomerRow.id.label("customer_id"),
                CustomerRow.telegram_user_id,
                CustomerRow.telegram_display_name.label("display_name"),
            )).mappings().one()
        debug_event("telegram.identity.linked")
        return dict(customer)

    def save_contact(self, business_id: str, customer_id: str, phone: str) -> None:
        with database_session(self.database_url) as session:
            if self.turn_guard:
                self.turn_guard(session)
            session.execute(update(CustomerRow).where(
                CustomerRow.business_id == business_id, CustomerRow.id == customer_id,
            ).values(phone=phone))
        debug_event("telegram.contact.saved")
