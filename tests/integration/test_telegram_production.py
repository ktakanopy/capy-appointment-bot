"""Exercise the multi-bot boundaries with disposable PostgreSQL and Redis."""

import logging
import os
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.booking import BookingService
from app.channels.bots import BotRegistry, TelegramBot
from app.channels.telegram import TelegramBotApi
from app.channels.telegram_delivery import TelegramOutbox
from app.channels.telegram_intake import save_update
from app.channels.telegram_repository import TelegramRepository
from app.channels.telegram_turns import ClaimLost, TelegramTurns
from app.config import load_settings
from app.database import database_session
from app.graph.builder import build_graph
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import DeterministicInterpreter
from app.main import app
from app.models import BookingCreateRequest, BookingRescheduleRequest
from app.runtime import close_runtime, create_runtime
from app.telegram_service import process_turn
from app.telegram_service import register as register_bot
from tests.integration.helpers import connect, next_open_day, offered_slots


def update(number, chat=101, **content):
    message = {"chat": {"id": chat, "type": "private"}, "from": {"id": chat}, "date": 1000}
    message.update(content)
    return {"update_id": number, "message": message}


def register(url, bot_id=42, *, business="demo-studio"):
    bot = TelegramBot(bot_id, business, f"TEST_BOT_{bot_id}", f"TEST_SECRET_{bot_id}")
    BotRegistry(url).register(
        bot_id, business, bot.token_env, webhook_route=f"private_test_route_{bot_id}",
        webhook_secret_env=bot.webhook_secret_env,
    )
    return bot


def ready_updates(url):
    with connect(url) as connection:
        connection.execute("UPDATE telegram_updates SET ready_at=now()-interval '1 second'")


def test_register_configures_webhook_after_saving_route(scheduling_db, monkeypatch):
    route = "local_demo_webhook"
    secret = "synthetic-webhook-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "44:test-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secret)
    settings = load_settings()
    registered_webhooks = []

    def call(_api, method, payload=None):
        if method == "getMe":
            return {"id": 44}
        assert method == "setWebhook"
        bot = BotRegistry(settings.database_url).by_route(route)
        assert bot is not None and bot.bot_id == 44 and bot.accepts_webhook(secret)
        registered_webhooks.append(payload)
        return True

    monkeypatch.setattr(TelegramBotApi, "call", call)
    args = Namespace(
        business="demo-studio", token_env="TELEGRAM_BOT_TOKEN", route=route,
        webhook_secret_env="TELEGRAM_WEBHOOK_SECRET", webhook_base_url="http://example.test",
    )
    with pytest.raises(ValueError, match="HTTPS origin"):
        register_bot(settings, args)
    assert BotRegistry(settings.database_url).by_route(route) is None

    args.webhook_base_url = "https://example.test/"
    register_bot(settings, args)

    assert registered_webhooks == [{
        "url": f"https://example.test/telegram/{route}",
        "secret_token": secret,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
    }]


def test_webhook_dedupes_per_bot(scheduling_db, monkeypatch):
    url = scheduling_db["url"]
    with connect(url) as connection:
        connection.execute(
            "INSERT INTO businesses (id,name,category,timezone) VALUES "
            "('second-demo-studio','Other Studio','Massage','UTC')"
        )
    first = register(url)
    second = register(url, 43, business="second-demo-studio")
    for bot in (first, second):
        monkeypatch.setenv(bot.webhook_secret_env, f"test-webhook-secret-{bot.bot_id}")
    with TestClient(app) as client:
        for bot in (first, second):
            path = f"/telegram/private_test_route_{bot.bot_id}"
            headers = {"X-Telegram-Bot-Api-Secret-Token": f"test-webhook-secret-{bot.bot_id}"}
            assert client.post(path, json=update(10, text="/start")).status_code == 403
            assert client.post(path, headers=headers, content=b"x" * (256 * 1024 + 1)).status_code == 413
            assert client.post(path, headers=headers, content=b"{invalid").status_code == 400
            assert client.post(path, headers=headers, json=update(10, text="/start")).status_code == 204
            assert client.post(path, headers=headers, json=update(10, text="/start")).status_code == 204
            assert client.post(path, headers=headers, json={"update_id": 11, "edited_message": {}}).status_code == 204
    with connect(url) as connection:
        receipts = connection.execute(
            """SELECT bot_id,business_id,status,payload FROM telegram_updates
               WHERE bot_id IN (42,43) ORDER BY bot_id,update_id"""
        ).fetchall()
    assert [(r["business_id"], r["status"]) for r in receipts] == [
        ("demo-studio", "received"), ("demo-studio", "delivered"),
        ("second-demo-studio", "received"), ("second-demo-studio", "delivered"),
    ]
    assert receipts[1]["payload"] is receipts[3]["payload"] is None


def test_claims_isolate_chats_and_reject_stale_booking_and_checkpoint(scheduling_db):
    url = scheduling_db["url"]
    bot = register(url)
    for item in (update(1, voice={"file_id": "clip"}), update(2, text="1"),
                 update(3, chat=102, text="prices")):
        assert save_update(url, bot, item)
    ready_updates(url)
    turns = TelegramTurns(url)
    voice = turns.claim_chat(42, 101)
    assert voice.row["update_id"] == 1
    assert turns.claim_chat(42, 101) is None
    other_chat = turns.claim_chat(42, 102)
    assert other_chat.row["update_id"] == 3
    assert turns.claim_chat(42, 102) is None
    turns.finish(other_chat, "Prices", "other-checkpoint")
    with connect(url) as connection:
        connection.execute(
            "UPDATE telegram_conversations SET lease_until=now()-interval '1 second' WHERE chat_id=101"
        )
    recovery = turns.claim_chat(42, 101)
    assert recovery.interrupted and recovery.generation > voice.generation
    slot = offered_slots(scheduling_db)[0]
    bookings = BookingService(scheduling_db["repository"], turn_guard=voice.assert_owned)
    with pytest.raises(ClaimLost):
        bookings.create(BookingCreateRequest(
            business_id=bot.business_id, customer_id="unreachable",
            service_id=slot.service_id, professional_id=slot.professional_id,
            slot_id=slot.slot_id, starts_at=slot.starts_at,
        ), "expired-lease")
    with pytest.raises(ClaimLost):
        turns.finish(voice, "Old reply", "stale-checkpoint")
    process_turn(turns, recovery, None)
    follow_up = turns.claim_chat(42, 101)
    assert follow_up.row["update_id"] == 2
    assert follow_up.checkpoint_thread is None
    assert not turns.renew(voice)


@pytest.mark.parametrize("outcome", ["success", "worker_crash", "draft_expired"])
@pytest.mark.parametrize("first_input", ["text", "voice"])
def test_completed_checkpoint_carries_slot_offer_into_next_attempt(
    scheduling_db, monkeypatch, caplog, outcome, first_input
):
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Redis is required for checkpoint promotion")
    url = scheduling_db["url"]
    bot = register(url)
    monkeypatch.setenv(bot.token_env, "42:synthetic")
    runtime = create_runtime(load_settings().model_copy(update={"chat_redis_url": redis_url}))
    spoken_request = f"Book Shiatsu 60 min on {next_open_day()}"
    recordings = []

    def transcribe(recording, _catalog):
        recordings.append(recording)
        return spoken_request

    monkeypatch.setattr(TelegramBotApi, "get_file", lambda *_: b"synthetic voice")
    runtime.workflow = LangGraphWorkflow(build_graph(
        DeterministicInterpreter(), checkpointer=runtime.workflow.graph.checkpointer,
        transcribe=transcribe,
    ))
    caplog.set_level(logging.DEBUG, logger=runtime.logger.name)
    turns = TelegramTurns(url)
    chat = time.time_ns() % 1_000_000_000
    try:
        first_message = (
            {"text": spoken_request} if first_input == "text" else {"voice": {"file_id": "clip"}}
        )
        save_update(url, bot, update(1, chat=chat, **first_message))
        ready_updates(url)
        process_turn(turns, turns.claim_chat(42, chat), runtime)
        assert recordings == ([b"synthetic voice"] if first_input == "voice" else [])
        selection = update(2, chat=chat, text="1")
        selection["message"]["from"]["first_name"] = "DebugExamplePrivateName"
        save_update(url, bot, selection)
        ready_updates(url)
        second = turns.claim_chat(42, chat)
        assert second.checkpoint_thread
        if outcome == "draft_expired":
            runtime.workflow.graph.checkpointer.delete_thread(second.checkpoint_thread)
        process_turn(turns, second, runtime)
        with connect(url) as connection:
            assert connection.execute("SELECT count(*) AS total FROM bookings").fetchone()["total"] == 0
        if outcome != "draft_expired":
            save_update(url, bot, update(3, chat=chat, text="Yes, book it."))
            ready_updates(url)
            third = turns.claim_chat(42, chat)
            if outcome == "worker_crash":
                def lose_reply(*_):
                    raise RuntimeError("simulated loss after booking commit")

                with monkeypatch.context() as patch:
                    patch.setattr(turns, "finish", lose_reply)
                    with pytest.raises(RuntimeError, match="simulated loss"):
                        process_turn(turns, third, runtime)
                with connect(url) as connection:
                    connection.execute(
                        "UPDATE telegram_conversations SET lease_until=now()-interval '1 second' "
                        "WHERE bot_id=42 AND chat_id=%s", (chat,),
                    )
                recovery = turns.claim_chat(42, chat)
                assert recovery.interrupted
                process_turn(turns, recovery, runtime)
            else:
                process_turn(turns, third, runtime)
        with connect(url) as connection:
            booking = connection.execute(
                "SELECT count(*) AS total FROM bookings b JOIN telegram_identities i "
                "ON i.customer_id=b.customer_id WHERE i.bot_id=42 AND i.telegram_user_id=%s",
                (chat,),
            ).fetchone()
            assert booking["total"] == (0 if outcome == "draft_expired" else 1)
            jobs = connection.execute(
                "SELECT update_id,message FROM telegram_outbox WHERE chat_id=%s ORDER BY id", (chat,)
            ).fetchall()
            assert [job["update_id"] for job in jobs] == (
                [1, 2] if outcome == "draft_expired" else [1, 2, 3]
            )
            if outcome == "draft_expired":
                assert "expired" in jobs[-1]["message"]
            if outcome == "worker_crash":
                assert "check your appointments" in jobs[-1]["message"]
        assert "DebugExamplePrivateName" not in caplog.text
        assert "42:synthetic" not in caplog.text
    finally:
        close_runtime(runtime)


def test_sender_cooldown_is_shared_but_other_bot_can_send(scheduling_db):
    url = scheduling_db["url"]
    with connect(url) as connection:
        connection.execute(
            "INSERT INTO businesses (id,name,category,timezone) VALUES "
            "('second-demo-studio','Other Studio','Massage','UTC')"
        )
    first = register(url)
    second = register(url, 43, business="second-demo-studio")
    for bot, number in ((first, 1), (first, 2), (second, 3)):
        save_update(url, bot, update(number, chat=100 + number, text="/start"))
    ready_updates(url)
    turns = TelegramTurns(url)
    for bot, number in ((first, 1), (first, 2), (second, 3)):
        claim = turns.claim_chat(bot.bot_id, 100 + number)
        turns.finish(claim, "Hello", None)
    with connect(url) as connection:
        ids = {row["update_id"]: row["id"] for row in connection.execute(
            "SELECT id, update_id FROM telegram_outbox"
        ).fetchall()}
    sender_a, sender_b = TelegramOutbox(url), TelegramOutbox(url)
    job = sender_a.claim_by_id(ids[1])
    assert job["bot_id"] == first.bot_id
    assert sender_a.finish(job, "ready", retry_after=4000, bot_cooldown=4000)
    other = sender_b.claim_by_id(ids[3])
    assert other["bot_id"] == second.bot_id
    assert sender_a.claim_by_id(ids[2]) is None
    with connect(url) as connection:
        connection.execute(
            "UPDATE telegram_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",
            (other["id"],),
        )
        remaining = connection.execute(
            "SELECT extract(epoch FROM available_at-now()) AS seconds FROM telegram_bot_delivery WHERE bot_id=42"
        ).fetchone()["seconds"]
    assert remaining > 3900
    assert sender_a.recover_expired() == 1
    assert not sender_b.finish(other, "sent")
    assert sender_b.claim_by_id(ids[3]) is None


def test_bad_bot_credentials_defer_only_its_turn(scheduling_db, monkeypatch):
    url = scheduling_db["url"]
    bad, good = register(url), register(url, 43)
    monkeypatch.setenv(bad.token_env, "999:wrong-bot")
    monkeypatch.setenv(good.token_env, "43:synthetic")
    save_update(url, bad, update(1, text="/start"))
    save_update(url, good, update(2, text="/start"))
    ready_updates(url)
    runtime = create_runtime(load_settings())
    turns = TelegramTurns(url)
    try:
        process_turn(turns, turns.claim_chat(42, 101), runtime)
        process_turn(turns, turns.claim_chat(43, 101), runtime)
    finally:
        close_runtime(runtime)
    with connect(url) as connection:
        deferred = connection.execute(
            "SELECT status,last_error FROM telegram_updates WHERE bot_id=42"
        ).fetchone()
        job = connection.execute("SELECT bot_id FROM telegram_outbox").fetchone()
    assert deferred == {"status": "received", "last_error": "bot_credentials"}
    assert job["bot_id"] == 43


def test_late_update_requests_a_fresh_offer(scheduling_db):
    url = scheduling_db["url"]
    bot = register(url)
    turns = TelegramTurns(url)
    save_update(url, bot, update(20, date=2000, text="Newer turn"))
    ready_updates(url)
    turns.finish(turns.claim_chat(42, 101), "Available times", "published")
    save_update(url, bot, update(19, date=1999, text="1"))
    ready_updates(url)
    late = turns.claim_chat(42, 101)
    assert late.late
    process_turn(turns, late, None)
    with connect(url) as connection:
        reply = connection.execute(
            "SELECT message FROM telegram_outbox WHERE update_id=19"
        ).fetchone()
        last = connection.execute(
            "SELECT last_update_id FROM telegram_conversations"
        ).fetchone()
    assert "arrived late" in reply["message"]
    assert last["last_update_id"] == 20
