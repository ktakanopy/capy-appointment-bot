"""Save Telegram webhook updates before acknowledging receipt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.channels.bots import TelegramBot
from app.channels.telegram_queue import ChatReady
from app.database import database_session
from app.db_models import TelegramConversation, TelegramUpdate
from app.observability import debug_event


def telegram_integer(value: object) -> bool:
    return type(value) is int and 0 < value < 2**63


def private_message(row: dict) -> dict | None:
    message = (row.get("payload") or {}).get("message")
    if not isinstance(message, dict):
        return None
    chat, sender = message.get("chat"), message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return None
    user_id, chat_id = row.get("telegram_user_id"), row.get("chat_id")
    if (
        chat.get("type") != "private"
        or sender.get("is_bot")
        or not telegram_integer(user_id)
        or chat_id != user_id
        or sender.get("id") != user_id
        or chat.get("id", chat_id) != chat_id
    ):
        return None
    return message


@dataclass(frozen=True)
class IntakeResult:
    accepted: bool
    notification: ChatReady | None = None


def save_update(
    database_url: str, bot: TelegramBot, update: dict
) -> IntakeResult:
    """Persist a Telegram update and schedule supported private messages for work.

    Use (bot_id, update_id) as the idempotency key because update IDs are scoped
    to a bot. An update with an existing key counts as accepted without another
    inbox row. Store only fields the worker uses. Other valid
    updates get a completed receipt. Notifications refer only to committed work.
    """
    update_id = update.get("update_id")
    if not telegram_integer(update_id):
        debug_event("telegram.inbox.ignored", reason="invalid_update_id")
        return IntakeResult(False)
    message = update.get("message")
    message = message if isinstance(message, dict) else {}
    chat, sender = message.get("chat"), message.get("from")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    user_id = sender.get("id") if isinstance(sender, dict) else None
    supported = private_message({
        "payload": {"message": message},
        "chat_id": chat_id,
        "telegram_user_id": user_id,
    })
    payload = None
    if supported:
        # Retain only fields the worker understands, which limits stored customer data.
        payload = {"message": {
            key: message[key] for key in ("chat", "from", "text", "contact", "voice", "audio")
            if key in message
        }}
    message_date = message.get("date")
    date = message_date if telegram_integer(message_date) else None
    with database_session(database_url) as session:
        idempotency_key_columns = (TelegramUpdate.bot_id, TelegramUpdate.update_id)
        inserted_update_id = session.scalar(insert(TelegramUpdate).values(
            bot_id=bot.bot_id,
            update_id=update_id,
            business_id=bot.business_id,
            chat_id=chat_id if supported else None,
            telegram_user_id=user_id if supported else None,
            payload=payload,
            status="received" if supported else "delivered",
            message_date=date,
            ready_at=func.clock_timestamp() + timedelta(seconds=0.3),
            expires_at=func.clock_timestamp() + timedelta(days=30),
        ).on_conflict_do_nothing(
            index_elements=idempotency_key_columns,
        ).returning(TelegramUpdate.update_id))
        if supported and inserted_update_id is not None:
            session.execute(insert(TelegramConversation).values(
                bot_id=bot.bot_id, chat_id=chat_id, business_id=bot.business_id,
            ).on_conflict_do_nothing())
    debug_event(
        "telegram.inbox.recorded", bot_id=bot.bot_id, update_id=update_id,
        source="webhook",
        is_private=bool(supported), inserted=inserted_update_id is not None,
    )
    return IntakeResult(
        True, ChatReady(bot.bot_id, chat_id) if supported and inserted_update_id is not None else None,
    )
