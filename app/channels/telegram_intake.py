"""Save Telegram webhook updates before acknowledging receipt."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.channels.bots import TelegramBot
from app.channels.telegram_publications import schedule_publication
from app.database import database_session
from app.db_models import TelegramBotRow, TelegramConversation, TelegramUpdate
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


def save_update(
    database_url: str, bot: TelegramBot, update: dict
) -> bool:
    """Persist a Telegram update and schedule supported private messages for work.

    Telegram may retry an update, so its update ID is the idempotency key and a
    duplicate counts as accepted without creating another inbox row. Only
    supported private-message fields are stored; other valid updates receive a
    completed receipt so Telegram does not keep retrying them. Return False when
    the update ID is invalid or the bot is no longer enabled for webhook intake.
    """
    update_id = update.get("update_id")
    if not telegram_integer(update_id):
        debug_event("telegram.inbox.ignored", reason="invalid_update_id")
        return False
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
        # The bot may have been disabled after route lookup; recheck before accepting its update.
        registered = session.scalar(select(TelegramBotRow.bot_id).where(
            TelegramBotRow.bot_id == bot.bot_id,
            TelegramBotRow.business_id == bot.business_id,
            TelegramBotRow.enabled,
            TelegramBotRow.webhook_route.is_not(None),
        ).with_for_update(read=True))
        if registered is None:
            debug_event("telegram.inbox.ignored", bot_id=bot.bot_id, update_id=update_id,
                        reason="bot_not_registered_for_intake")
            return False
        # Telegram retries use the same ID, so this unique key prevents duplicate inbox work.
        receipt_id = session.scalar(insert(TelegramUpdate).values(
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
            index_elements=["bot_id", "update_id"],
        ).returning(TelegramUpdate.update_id))
        if supported and receipt_id is not None:
            session.execute(insert(TelegramConversation).values(
                bot_id=bot.bot_id, chat_id=chat_id, business_id=bot.business_id,
            ).on_conflict_do_nothing())
            schedule_publication(session, bot_id=bot.bot_id, chat_id=chat_id, delay=0.3)
    debug_event(
        "telegram.inbox.recorded", bot_id=bot.bot_id, update_id=update_id,
        source="webhook",
        is_private=bool(supported), inserted=receipt_id is not None,
    )
    return True
