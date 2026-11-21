"""Exercise the worker from the real webhook through a saved reply."""

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from app.channels.telegram import TelegramBotApi
from app.config import load_settings
from app.main import app
from app.telegram_worker import run_worker
from app.telegram_sender import run_sender
from tests.integration.helpers import connect
from tests.integration.test_telegram_production import update


def test_worker_delivers_a_saved_reply_without_a_broker(scheduling_db, monkeypatch):
    url = scheduling_db["url"]
    monkeypatch.setenv("TELEGRAM_BOT_ID", "42")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:synthetic")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    sent, stop = Event(), Event()
    replies = []

    def send_message(_api, chat_id, message, _reply_markup):
        replies.append((chat_id, message))
        sent.set()

    monkeypatch.setattr(TelegramBotApi, "send_message", send_message)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post(
                "/telegram/webhook", json=update(1, text="/start"),
                headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"},
            )
            assert response.status_code == 204

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(role, load_settings(), stop, logging.getLogger(__name__))
                for role in (run_worker, run_sender)]
        try:
            assert sent.wait(5)
        finally:
            stop.set()
            for job in jobs:
                job.result(timeout=5)

    with connect(url) as connection:
        assert connection.execute("SELECT status FROM telegram_outbox").fetchall() == [{"status": "sent"}]
    assert len(replies) == 1 and replies[0][0] == 101
