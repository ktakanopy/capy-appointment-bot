"""Resolve a Telegram bot to its configured business and secret references."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from hmac import compare_digest

from app.channels.telegram import TelegramBotApi
from app.database import connect
from app.observability import debug_event


@dataclass(frozen=True)
class TelegramBot:
    bot_id: int
    business_id: str
    token_env: str
    webhook_secret_env: str | None = None

    def client(self) -> TelegramBotApi:
        token = os.getenv(self.token_env)
        if not token:
            raise RuntimeError("The registered Telegram bot token is unavailable.")
        prefix, separator, _ = token.partition(":")
        if not separator or prefix != str(self.bot_id):
            raise RuntimeError("The configured Telegram token belongs to a different bot.")
        return TelegramBotApi(token)

    def accepts_webhook(self, supplied: str) -> bool:
        secret = os.getenv(self.webhook_secret_env, "") if self.webhook_secret_env else ""
        if len(secret) < 16:
            return False
        return compare_digest(secret.encode(), supplied.encode())


class BotRegistry:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def by_route(self, route: str) -> TelegramBot | None:
        with connect(self.database_url) as connection:
            row = connection.execute(
                """SELECT bot_id, business_id, token_env, webhook_secret_env
                   FROM telegram_bots WHERE enabled AND webhook_route=%s""",
                (route,),
            ).fetchone()
        debug_event("telegram.bot.lookup", found=row is not None)
        return TelegramBot(**row) if row else None

    def register(
        self,
        bot_id: int,
        business_id: str,
        token_env: str,
        *,
        webhook_route: str | None = None,
        webhook_secret_env: str | None = None,
        reminder_delivery: bool = False,
    ) -> None:
        """Register a verified bot without ever changing its tenant."""
        names = (token_env, webhook_secret_env) if webhook_secret_env else (token_env,)
        if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) for name in names):
            raise ValueError("Use environment variable names for Telegram secret references.")
        if (webhook_route is None) != (webhook_secret_env is None):
            raise ValueError("A webhook route and secret reference must be set together.")
        if webhook_route and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", webhook_route):
            raise ValueError("Use an opaque route ID of 16 to 128 URL-safe characters.")
        with connect(self.database_url) as connection:
            self._check_legacy_scope(connection, bot_id, business_id)
            existing = connection.execute(
                """INSERT INTO telegram_bots
                       (bot_id, business_id, token_env, webhook_route, webhook_secret_env,
                        reminder_delivery)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (bot_id) DO UPDATE SET
                       token_env=EXCLUDED.token_env,
                       webhook_route=EXCLUDED.webhook_route,
                       webhook_secret_env=EXCLUDED.webhook_secret_env,
                       reminder_delivery=EXCLUDED.reminder_delivery
                   WHERE telegram_bots.business_id=EXCLUDED.business_id
                   RETURNING bot_id""",
                (bot_id, business_id, token_env, webhook_route, webhook_secret_env,
                 reminder_delivery),
            ).fetchone()
            if not existing:
                raise ValueError("This bot is already registered to a different business.")
            self._backfill_conversations(connection, bot_id, business_id)
        debug_event("telegram.bot.registered", bot_id=bot_id, reminders=reminder_delivery)

    @staticmethod
    def _check_legacy_scope(connection, bot_id: int, business_id: str) -> None:
        conflict = connection.execute(
            """SELECT 1 FROM telegram_identities WHERE bot_id=%s AND business_id<>%s
               UNION ALL
               SELECT 1 FROM telegram_updates WHERE bot_id=%s AND business_id<>%s
               LIMIT 1""",
            (bot_id, business_id, bot_id, business_id),
        ).fetchone()
        if conflict:
            raise ValueError("The bot has legacy records for a different business.")

    @staticmethod
    def _backfill_conversations(connection, bot_id: int, business_id: str) -> None:
        connection.execute(
            """UPDATE telegram_updates SET status='delivered', payload=NULL,
                   last_error='unsupported_legacy_update', expires_at=now()+interval '30 days'
               WHERE bot_id=%s AND business_id=%s AND status IN ('received','processing')
                 AND NOT COALESCE(chat_id>0 AND chat_id=telegram_user_id
                     AND payload->'message'->'chat'->>'type'='private', false)""",
            (bot_id, business_id),
        )
        connection.execute(
            """INSERT INTO telegram_conversations (bot_id, chat_id, business_id)
               SELECT DISTINCT bot_id, chat_id, business_id FROM telegram_updates
               WHERE bot_id=%s AND business_id=%s AND status IN ('received','processing')
               ON CONFLICT DO NOTHING""",
            (bot_id, business_id),
        )
