"""Resolve the single Telegram bot from the deployment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hmac import compare_digest

from app.channels.telegram import TelegramBotApi
from app.config import Settings


@dataclass(frozen=True)
class TelegramBot:
    bot_id: int
    business_id: str
    token_env: str
    webhook_secret_env: str | None = None

    def client(self) -> TelegramBotApi:
        token = os.getenv(self.token_env)
        if not token:
            raise RuntimeError("The Telegram bot token is unavailable.")
        prefix, separator, _ = token.partition(":")
        if not separator or prefix != str(self.bot_id):
            raise RuntimeError(
                "The configured Telegram token belongs to a different bot."
            )
        return TelegramBotApi(token)

    def accepts_webhook(self, supplied: str) -> bool:
        secret = (
            os.getenv(self.webhook_secret_env, "") if self.webhook_secret_env else ""
        )
        if len(secret) < 16:
            return False
        return compare_digest(secret.encode(), supplied.encode())


def configured_bot(settings: Settings) -> TelegramBot | None:
    if settings.telegram_bot_id is None:
        return None
    return TelegramBot(
        settings.telegram_bot_id, settings.demo_business_id,
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET",
    )
