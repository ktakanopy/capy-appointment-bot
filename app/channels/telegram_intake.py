"""Save Telegram messages before advancing a poll cursor or acknowledging a webhook."""

from __future__ import annotations

from app.channels.bots import TelegramBot
from app.database import connect
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
    database_url: str, bot: TelegramBot, update: dict, *, polling: bool = False
) -> bool:
    """Return whether the update has a valid receipt ID and an active bot."""
    update_id = update.get("update_id")
    if not telegram_integer(update_id):
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
                 AND (webhook_route IS NULL)=%s FOR SHARE""",
            (bot.bot_id, bot.business_id, polling),
        ).fetchone()
        if not registered:
            return False
        connection.execute(
            """INSERT INTO telegram_updates
                   (bot_id, update_id, business_id, chat_id, telegram_user_id, payload, status,
                    message_date, ready_at, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                       clock_timestamp() + %s * interval '1 second',
                       clock_timestamp() + interval '30 days')
               ON CONFLICT (bot_id, update_id) DO NOTHING""",
            (bot.bot_id, update_id, bot.business_id, chat_id if supported else None,
             user_id if supported else None, Jsonb(payload) if supported else None,
             "received" if supported else "delivered", date, 0 if polling else 0.3),
        )
        if supported:
            connection.execute(
                """INSERT INTO telegram_conversations (bot_id, chat_id, business_id)
                   VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                (bot.bot_id, chat_id, bot.business_id),
            )
        if polling:
            connection.execute(
                """INSERT INTO telegram_poll_offsets (bot_id, next_update_id)
                   VALUES (%s,%s) ON CONFLICT (bot_id) DO UPDATE
                   SET next_update_id=EXCLUDED.next_update_id, updated_at=now()""",
                (bot.bot_id, update_id + 1),
            )
    return True
