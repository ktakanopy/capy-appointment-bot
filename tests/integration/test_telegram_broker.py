import asyncio
import logging
from threading import Event
from unittest.mock import Mock

import aio_pika
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.channels.telegram_intake import save_update
from app.channels.telegram_queue import ChatReady, QUEUE_NAME, TelegramQueue
from app.channels.telegram_turns import TelegramTurns
from app.main import app
from app.telegram_sender import run_sender
from app.config import load_settings
from tests.integration.helpers import connect
from tests.integration.test_telegram_processors import until
from tests.integration.test_telegram_production import ready_updates, register, update

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_real_transport_validates_payload_and_bounds_unroutable_publication(rabbit_url, caplog):
    caplog.set_level(logging.INFO)
    queue = TelegramQueue(rabbit_url, 42, LOGGER, consume=True)
    queue.start()
    try:
        await until(lambda: queue.connected)
        assert await queue.try_publish(ChatReady(42, 101))
        delivery = await queue.next_delivery(2)
        assert delivery.notification == ChatReady(42, 101)
        assert delivery.message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
        assert await queue.acknowledge(delivery)
        for body in (b"broken", b'{"bot_id":true,"chat_id":101}', b'{"bot_id":43,"chat_id":101}',
                     b'{"bot_id":42,"chat_id":101,"message":"private"}', b"x" * 257):
            await queue._channel.default_exchange.publish(aio_pika.Message(body), routing_key=QUEUE_NAME)
        assert await queue.next_delivery(0.1) is None
        assert "malformed" in caplog.text
        assert "out_of_scope" in caplog.text
        assert "private" not in caplog.text
        await queue._channel.queue_delete(QUEUE_NAME)
        assert not await queue.try_publish(ChatReady(42, 101), timeout=0.2)
    finally:
        await queue.close()


def test_webhook_commits_before_publication_and_preserves_completed_duplicates(scheduling_db, monkeypatch):
    url = scheduling_db["url"]
    monkeypatch.setenv("TELEGRAM_BOT_ID", "42")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    observed = []

    async def failed_publish(_self, notification, timeout):
        with connect(url) as connection:
            row = connection.execute("SELECT status FROM telegram_updates WHERE update_id=1").fetchone()
            assert row["status"] == "received"
        observed.append(notification)
        return False

    monkeypatch.setattr(TelegramQueue, "try_publish", failed_publish)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}
    with TestClient(app) as client:
        assert client.post("/telegram/webhook", json=update(1, text="accepted"), headers=headers).status_code == 204
        ready_updates(url)
        turns = TelegramTurns(url, register(url))
        turns.finish(turns.claim_next(), "Saved", "checkpoint")
        assert client.post("/telegram/webhook", json=update(1, text="replacement"), headers=headers).status_code == 204
        assert client.post("/telegram/webhook", json={"update_id": 2}, headers=headers).status_code == 204
        assert client.post("/telegram/webhook", json={"update_id": True}, headers=headers).status_code == 400
        assert client.get("/health").json()["broker"] == "unavailable"
        assert client.get("/live").status_code == 200
    assert observed == [ChatReady(42, 101)]
    with connect(url) as connection:
        rows = connection.execute("SELECT status,payload FROM telegram_updates ORDER BY update_id").fetchall()
        assert rows == [{"status": "delivered", "payload": None}] * 2


def test_database_failure_is_not_accepted(scheduling_db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_ID", "42")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr("app.main.save_update", Mock(side_effect=OperationalError("", {}, Exception())))
    with TestClient(app) as client:
        response = client.post("/telegram/webhook", json=update(1), headers={
            "X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret",
        })
        assert response.status_code == 503


def test_second_sender_cannot_enter_loop_and_detects_lost_lock(scheduling_db, monkeypatch):
    from app.telegram_sender import acquire_sender_lock, send_pending
    from app.channels.telegram_delivery import TelegramOutbox
    import psycopg

    url = scheduling_db["url"]
    monkeypatch.setenv("TELEGRAM_BOT_ID", "42")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:synthetic")
    lock = acquire_sender_lock(url, 42)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            run_sender(load_settings(), Event(), LOGGER)
        with connect(url) as connection:
            connection.execute("SELECT pg_terminate_backend(%s)", (lock.info.backend_pid,))
        outbox = TelegramOutbox(url, register(url))
        claim = Mock(side_effect=AssertionError("lost lock must prevent delivery"))
        monkeypatch.setattr(outbox, "claim_next", claim)
        with pytest.raises(psycopg.OperationalError):
            send_pending(outbox, Event(), LOGGER, lock)
        claim.assert_not_called()
    finally:
        lock.close()


@pytest.mark.parametrize("code,expected", [("telegram_api_429", "ready"), ("telegram_transport_error", "ambiguous"), ("telegram_http_403", "failed")])
def test_sender_preserves_classification_and_persisted_cooldown(scheduling_db, monkeypatch, code, expected):
    from app.channels.telegram import TelegramApiError, TelegramBotApi
    from app.channels.telegram_delivery import TelegramOutbox
    from app.telegram_sender import deliver_claimed

    url = scheduling_db["url"]
    bot = register(url)
    monkeypatch.setenv(bot.token_env, "42:synthetic")
    save_update(url, bot, update(1, text="hello"))
    ready_updates(url)
    turns = TelegramTurns(url, bot)
    turns.finish(turns.claim_next(), "Reply", None)
    monkeypatch.setattr(TelegramBotApi, "send_message", Mock(side_effect=TelegramApiError(code, retry_after=17)))
    outbox = TelegramOutbox(url, bot)
    deliver_claimed(outbox, LOGGER, outbox.claim_next())
    with connect(url) as connection:
        row = connection.execute("SELECT status,last_error,ready_at>now() AS delayed FROM telegram_outbox").fetchone()
        assert row["status"] == expected
        assert row["last_error"] == code
        assert row["delayed"]
    assert outbox.claim_next() is None
