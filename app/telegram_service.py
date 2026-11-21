"""Run the single Telegram worker or configure its webhook."""

import argparse
import os
import re
import signal
from threading import Event
from urllib.parse import urlsplit

from app.channels.bots import configured_bot
from app.config import load_settings
from app.observability import get_logger, log_event
from app.telegram_worker import run_worker


def register(settings, args) -> None:
    bot = configured_bot(settings)
    if bot is None:
        raise ValueError("Set TELEGRAM_BOT_ID before registering the webhook.")
    base_url = urlsplit(args.webhook_base_url or "")
    if (
        base_url.scheme != "https"
        or not base_url.hostname
        or base_url.username is not None
        or base_url.password is not None
        or base_url.path not in {"", "/"}
        or base_url.query
        or base_url.fragment
    ):
        raise ValueError("Use an HTTPS origin such as https://bot.example.com.")
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", secret):
        raise ValueError("Set a webhook secret of 16 to 256 URL-safe characters.")
    client = bot.client()
    if client.get_me()["id"] != bot.bot_id:
        raise ValueError("TELEGRAM_BOT_ID does not match the bot token.")
    client.call("setWebhook", {
        "url": f"https://{base_url.netloc}/telegram/{settings.telegram_webhook_route}",
        "secret_token": secret,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
    })
    log_event(get_logger(), "telegram.webhook.configured", bot_id=bot.bot_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("process", "register"))
    parser.add_argument("--webhook-base-url")
    args = parser.parse_args()
    settings = load_settings()
    if args.role == "register":
        register(settings, args)
        return

    stopping = Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopping.set())
    run_worker(settings, stopping, get_logger())


if __name__ == "__main__":
    main()
