"""Resolve a Telegram bot to its configured business and secret references."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta
from hmac import compare_digest

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.channels.telegram import TelegramBotApi
from app.database import database_session
from app.db_models import TelegramBotRow, TelegramConversation, TelegramIdentity, TelegramUpdate
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
        statement = select(
            TelegramBotRow.bot_id, TelegramBotRow.business_id,
            TelegramBotRow.token_env, TelegramBotRow.webhook_secret_env,
        ).where(TelegramBotRow.enabled, TelegramBotRow.webhook_route == route)
        with database_session(self.database_url) as session:
            row = session.execute(statement).mappings().first()
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
        with database_session(self.database_url) as session:
            self._check_legacy_scope(session, bot_id, business_id)
            bot = insert(TelegramBotRow).values(
                bot_id=bot_id, business_id=business_id, token_env=token_env,
                webhook_route=webhook_route, webhook_secret_env=webhook_secret_env,
                reminder_delivery=reminder_delivery,
            )
            registered = session.scalar(bot.on_conflict_do_update(
                index_elements=["bot_id"],
                set_={
                    "token_env": bot.excluded.token_env,
                    "webhook_route": bot.excluded.webhook_route,
                    "webhook_secret_env": bot.excluded.webhook_secret_env,
                    "reminder_delivery": bot.excluded.reminder_delivery,
                },
                where=TelegramBotRow.business_id == bot.excluded.business_id,
            ).returning(TelegramBotRow.bot_id))
            if registered is None:
                raise ValueError("This bot is already registered to a different business.")
            self._backfill_conversations(session, bot_id, business_id)
        debug_event("telegram.bot.registered", bot_id=bot_id, reminders=reminder_delivery)

    @staticmethod
    def _check_legacy_scope(session: Session, bot_id: int, business_id: str) -> None:
        different_identity = select(TelegramIdentity.bot_id).where(
            TelegramIdentity.bot_id == bot_id, TelegramIdentity.business_id != business_id,
        ).exists()
        different_update = select(TelegramUpdate.bot_id).where(
            TelegramUpdate.bot_id == bot_id, TelegramUpdate.business_id != business_id,
        ).exists()
        if session.scalar(select(different_identity | different_update)):
            raise ValueError("The bot has legacy records for a different business.")

    @staticmethod
    def _backfill_conversations(session: Session, bot_id: int, business_id: str) -> None:
        unfinished = (
            TelegramUpdate.bot_id == bot_id, TelegramUpdate.business_id == business_id,
            TelegramUpdate.status.in_(("received", "processing")),
        )
        private_chat = (
            (TelegramUpdate.chat_id > 0)
            & (TelegramUpdate.chat_id == TelegramUpdate.telegram_user_id)
            & (TelegramUpdate.payload["message"]["chat"]["type"].astext == "private")
        )
        session.execute(update(TelegramUpdate).where(
            *unfinished, ~func.coalesce(private_chat, False),
        ).values(
            status="delivered", payload=None, last_error="unsupported_legacy_update",
            expires_at=func.now() + timedelta(days=30),
        ))
        chats = select(TelegramUpdate.bot_id, TelegramUpdate.chat_id, TelegramUpdate.business_id).where(
            *unfinished,
        ).distinct()
        session.execute(insert(TelegramConversation).from_select(
            ["bot_id", "chat_id", "business_id"], chats,
        ).on_conflict_do_nothing())
