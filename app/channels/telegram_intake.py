"""Save Telegram webhook updates before acknowledging receipt."""

from __future__ import annotations

from app.channels.bots import TelegramBot
from app.channels.telegram_publications import schedule_publication
from app.database import connect
from app.observability import debug_event
from psycopg.types.json import Jsonb


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
    """Return whether the update has a valid receipt ID and an active bot."""
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
        payload = {"message": {
            key: message[key] for key in ("chat", "from", "text", "contact", "voice", "audio")
            if key in message
        }}
    message_date = message.get("date")
    date = message_date if telegram_integer(message_date) else None
    with connect(database_url) as connection:
        registered = connection.execute(
            """SELECT bot_id FROM telegram_bots
               WHERE bot_id=%s AND business_id=%s AND enabled
                 AND webhook_route IS NOT NULL FOR SHARE""",
            (bot.bot_id, bot.business_id),
        ).fetchone()
        if not registered:
            debug_event("telegram.inbox.ignored", bot_id=bot.bot_id, update_id=update_id,
                        reason="bot_not_registered_for_intake")
            return False
        receipt = connection.execute(
            """INSERT INTO telegram_updates
                   (bot_id, update_id, business_id, chat_id, telegram_user_id, payload, status,
                    message_date, ready_at, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                       clock_timestamp() + interval '0.3 seconds',
                       clock_timestamp() + interval '30 days')
               ON CONFLICT (bot_id, update_id) DO NOTHING""",
            (bot.bot_id, update_id, bot.business_id, chat_id if supported else None,
             user_id if supported else None, Jsonb(payload) if supported else None,
             "received" if supported else "delivered", date),
        )
        if supported and receipt.rowcount:
            connection.execute(
                """INSERT INTO telegram_conversations (bot_id, chat_id, business_id)
                   VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                (bot.bot_id, chat_id, bot.business_id),
            )
            schedule_publication(connection, bot_id=bot.bot_id, chat_id=chat_id, delay=0.3)
    debug_event(
        "telegram.inbox.recorded", bot_id=bot.bot_id, update_id=update_id,
        source="webhook",
        is_private=bool(supported), inserted=receipt.rowcount == 1,
    )
    return True
