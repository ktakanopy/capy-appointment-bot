"""Exercise the multi-bot boundaries with disposable PostgreSQL and Redis."""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.booking import BookingService
from app.channels.bots import BotRegistry, TelegramBot
from app.channels.telegram_delivery import REPLY_BURST, TelegramOutbox
from app.channels.telegram_intake import save_update
from app.channels.telegram_repository import TelegramRepository
from app.channels.telegram_turns import ClaimLost, TelegramTurns
from app.config import load_settings
from app.database import connect
from app.graph.builder import build_graph
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import DeterministicInterpreter
from app.main import app
from app.models import BookingCreateRequest, BookingRescheduleRequest
from app.reminders import ReminderWorker, enqueue_telegram_reminders
from app.runtime import close_runtime, create_runtime
from app.seed import seed
from app.telegram_service import process_turn, reconcile_replies
from tests.integration.helpers import next_open_day, offered_slots


def update(number, chat=101, **content):
    message = {"chat": {"id": chat, "type": "private"}, "from": {"id": chat}, "date": 1000}
    message.update(content)
    return {"update_id": number, "message": message}


def register(url, bot_id=42, *, business="demo-studio", reminders=False):
    bot = TelegramBot(bot_id, business, f"TEST_BOT_{bot_id}", f"TEST_SECRET_{bot_id}")
    BotRegistry(url).register(
        bot_id, business, bot.token_env, webhook_route=f"private_test_route_{bot_id}",
        webhook_secret_env=bot.webhook_secret_env, reminder_delivery=reminders,
    )
    return bot


def ready_updates(url):
    with connect(url) as connection:
        connection.execute("UPDATE telegram_updates SET ready_at=now()-interval '1 second'")


def test_webhook_dedupes_per_bot_and_keeps_poll_offsets(scheduling_db, monkeypatch):
    url = scheduling_db["url"]
    seed(include_second_business=True)
    first = register(url)
    second = register(url, 43, business="second-demo-studio")
    with connect(url) as connection:
        offsets_before = connection.execute(
            "SELECT * FROM telegram_poll_offsets WHERE bot_id IN (42,43) ORDER BY bot_id"
        ).fetchall()
    for bot in (first, second):
        monkeypatch.setenv(bot.webhook_secret_env, f"test-webhook-secret-{bot.bot_id}")
    with TestClient(app) as client:
        for bot in (first, second):
            path = f"/telegram/private_test_route_{bot.bot_id}"
            headers = {"X-Telegram-Bot-Api-Secret-Token": f"test-webhook-secret-{bot.bot_id}"}
            assert client.post(path, json=update(10, text="/start")).status_code == 403
            assert client.post(path, headers=headers, content=b"x" * (256 * 1024 + 1)).status_code == 413
            assert client.post(path, headers=headers, json=update(10, text="/start")).status_code == 204
            assert client.post(path, headers=headers, json=update(10, text="/start")).status_code == 204
            assert client.post(path, headers=headers, json={"update_id": 11, "edited_message": {}}).status_code == 204
    with connect(url) as connection:
        receipts = connection.execute(
            """SELECT bot_id,business_id,status,payload FROM telegram_updates
               WHERE bot_id IN (42,43) ORDER BY bot_id,update_id"""
        ).fetchall()
        assert connection.execute(
            "SELECT * FROM telegram_poll_offsets WHERE bot_id IN (42,43) ORDER BY bot_id"
        ).fetchall() == offsets_before
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
    voice = turns.claim(lane="audio")
    assert voice.row["update_id"] == 1
    assert turns.claim(lane="text", per_business=1) is None
    other_chat = turns.claim(lane="text")
    assert other_chat.row["update_id"] == 3
    assert turns.claim() is None
    turns.finish(other_chat, "Prices", "other-checkpoint")
    with connect(url) as connection:
        connection.execute(
            "UPDATE telegram_conversations SET lease_until=now()-interval '1 second' WHERE chat_id=101"
        )
    recovery = turns.claim()
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
    follow_up = turns.claim()
    assert follow_up.row["update_id"] == 2
    assert follow_up.checkpoint_thread is None
    assert not turns.renew(voice)


@pytest.mark.parametrize("outcome", ["success", "worker_crash", "draft_expired"])
def test_completed_checkpoint_carries_slot_offer_into_next_attempt(
    scheduling_db, monkeypatch, caplog, outcome
):
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Redis is required for checkpoint promotion")
    url = scheduling_db["url"]
    bot = register(url)
    monkeypatch.setenv(bot.token_env, "42:synthetic")
    runtime = create_runtime(replace(load_settings(), chat_redis_url=redis_url))
    runtime.workflow = LangGraphWorkflow(build_graph(
        DeterministicInterpreter(), checkpointer=runtime.workflow.graph.checkpointer
    ))
    caplog.set_level(logging.DEBUG, logger=runtime.logger.name)
    turns = TelegramTurns(url)
    chat = time.time_ns() % 1_000_000_000
    try:
        save_update(url, bot, update(1, chat=chat, text=f"Book Massage {next_open_day()}"))
        ready_updates(url)
        first = turns.claim()
        process_turn(turns, first, runtime)
        selection = update(2, chat=chat, text="1")
        selection["message"]["from"]["first_name"] = "DebugExamplePrivateName"
        save_update(url, bot, selection)
        ready_updates(url)
        second = turns.claim()
        assert second.checkpoint_thread
        if outcome == "worker_crash":
            def lose_reply(*_):
                raise RuntimeError("simulated loss after booking commit")

            with monkeypatch.context() as patch:
                patch.setattr(turns, "finish", lose_reply)
                with pytest.raises(RuntimeError, match="simulated loss"):
                    process_turn(turns, second, runtime)
            with connect(url) as connection:
                connection.execute(
                    """UPDATE telegram_conversations SET lease_until=now()-interval '1 second'
                       WHERE bot_id=42 AND chat_id=%s""",
                    (chat,),
                )
            recovery = turns.claim()
            assert recovery.interrupted
            process_turn(turns, recovery, runtime)
        else:
            if outcome == "draft_expired":
                runtime.workflow.graph.checkpointer.delete_thread(second.checkpoint_thread)
            process_turn(turns, second, runtime)
        with connect(url) as connection:
            booking = connection.execute(
                "SELECT count(*) AS total FROM bookings WHERE telegram_bot_id=42 AND telegram_chat_id=%s",
                (chat,),
            ).fetchone()
            assert booking["total"] == (0 if outcome == "draft_expired" else 1)
            jobs = connection.execute(
                "SELECT update_id,message FROM telegram_outbox WHERE chat_id=%s ORDER BY id", (chat,)
            ).fetchall()
            assert [job["update_id"] for job in jobs] == [1, 2]
            if outcome == "draft_expired":
                assert "expired" in jobs[-1]["message"]
            if outcome == "worker_crash":
                assert "check your appointments" in jobs[-1]["message"]
        if outcome == "success":
            expected = [
                "telegram.inbox.recorded", "telegram.turn.claimed",
                "workflow.checkpoint.forked", "workflow.slot.matched",
                "availability.slot.validated", "reminder.write.staged",
                "booking.saved", "telegram.turn.saved",
            ]
            events = [
                event["event"] for record in caplog.records
                if isinstance(event := record.msg, dict)
                and event.get("event") in expected
                and event.get("bot_id") == 42 and event.get("update_id") == 2
            ]
            assert events == expected
        assert "DebugExamplePrivateName" not in caplog.text
        assert "42:synthetic" not in caplog.text
    finally:
        close_runtime(runtime)


def test_reschedule_and_cancel_supersede_materialized_reminders(scheduling_db):
    url = scheduling_db["url"]
    bot = register(url, reminders=True)
    identity = TelegramRepository(url).ensure_customer_for_sender(
        bot.business_id, bot.bot_id, 101, "Synthetic customer"
    )
    service = BookingService(scheduling_db["repository"], telegram_destination=(bot.bot_id, 101))
    available = offered_slots(scheduling_db)
    slot, next_slot = available[0], available[-1]
    created = service.create(BookingCreateRequest(
        business_id=bot.business_id, customer_id=identity["customer_id"],
        service_id=slot.service_id, professional_id=slot.professional_id,
        slot_id=slot.slot_id, starts_at=slot.starts_at,
    ), "create")

    def force_due():
        with connect(url) as connection:
            connection.execute("UPDATE reminders SET send_at=now()-interval '1 minute'")

    force_due()
    assert ReminderWorker(url).deliver_due() == 0
    with ThreadPoolExecutor(max_workers=1) as workers:
        with connect(url) as connection:
            service._lock_business_and_check_retry(connection, bot.business_id, "cancel", "race", "hash")
            # A scheduler must be able to insert its FK while the booking
            # transaction holds the write-serialization lock on the business.
            assert workers.submit(enqueue_telegram_reminders, url).result(timeout=3) == 1
    assert enqueue_telegram_reminders(url) == 0
    service.reschedule(
        bot.business_id, identity["customer_id"], created.booking.id,
        BookingRescheduleRequest(
            business_id=bot.business_id, service_id=next_slot.service_id,
            professional_id=next_slot.professional_id, slot_id=next_slot.slot_id,
            starts_at=next_slot.starts_at,
        ), "move",
    )
    force_due()
    assert enqueue_telegram_reminders(url) == 1
    outbox = TelegramOutbox(url)
    job = outbox.claim()
    assert job["reminder_generation"] == 2
    service.cancel(bot.business_id, identity["customer_id"], created.booking.id, "cancel")
    assert outbox.finish(job, "sent")  # Telegram may already have accepted it.
    assert outbox.claim() is None
    with connect(url) as connection:
        reminder = connection.execute("SELECT status,generation FROM reminders").fetchone()
        jobs = connection.execute("SELECT status FROM telegram_outbox ORDER BY id").fetchall()
    assert reminder == {"status": "canceled", "generation": 3}
    assert [row["status"] for row in jobs] == ["canceled", "sent"]


def test_locked_reminder_does_not_block_another_bot(scheduling_db):
    url = scheduling_db["url"]
    bot, other = register(url, reminders=True), register(url, 43)
    identity = TelegramRepository(url).ensure_customer_for_sender(bot.business_id, bot.bot_id, 101, "Customer")
    service = BookingService(scheduling_db["repository"], telegram_destination=(bot.bot_id, 101))
    slot = offered_slots(scheduling_db)[0]
    booking = service.create(BookingCreateRequest(
        business_id=bot.business_id, customer_id=identity["customer_id"],
        service_id=slot.service_id, professional_id=slot.professional_id,
        slot_id=slot.slot_id, starts_at=slot.starts_at,
    ), "test-create").booking
    with connect(url) as connection:
        connection.execute("UPDATE reminders SET send_at=now()-interval '1 minute'")
    enqueue_telegram_reminders(url)
    save_update(url, other, update(10, text="/start"))
    ready_updates(url)
    turns = TelegramTurns(url)
    turns.finish(turns.claim(), "Other bot reply", None)
    with connect(url) as connection:
        connection.execute(
            "INSERT INTO telegram_bot_delivery (bot_id, reply_streak) VALUES (%s,%s)",
            (bot.bot_id, REPLY_BURST),
        )
        connection.execute(
            "UPDATE telegram_outbox SET ready_at=now()-interval '1 minute' WHERE reminder_id IS NOT NULL"
        )
    with ThreadPoolExecutor(max_workers=1) as workers:
        with connect(url) as connection:
            # The earlier readiness timestamp puts this locked reminder ahead of the other bot's reply.
            connection.execute("SELECT id FROM bookings WHERE id=%s FOR UPDATE", (booking.id,))
            job = workers.submit(TelegramOutbox(url).claim).result(timeout=3)
            assert job["bot_id"] == other.bot_id


def test_sender_cooldown_is_shared_but_other_bot_can_send(scheduling_db):
    url = scheduling_db["url"]
    seed(include_second_business=True)
    first = register(url)
    second = register(url, 43, business="second-demo-studio")
    for bot, number in ((first, 1), (first, 2), (second, 3)):
        save_update(url, bot, update(number, chat=100 + number, text="/start"))
    ready_updates(url)
    turns = TelegramTurns(url)
    for _ in range(3):
        claim = turns.claim()
        turns.finish(claim, "Hello", None)
    sender_a, sender_b = TelegramOutbox(url), TelegramOutbox(url)
    job = sender_a.claim()
    assert job["bot_id"] == first.bot_id
    assert sender_a.finish(job, "ready", retry_after=4000, bot_cooldown=4000)
    other = sender_b.claim()
    assert other["bot_id"] == second.bot_id
    assert sender_a.claim() is None
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
    assert sender_b.claim() is None


def test_legacy_replies_backfill_once(scheduling_db):
    url = scheduling_db["url"]
    repo = TelegramRepository(url)
    bot = register(url)
    repo.save_update(
        bot_id=42, update_id=1, business_id="demo-studio", chat_id=101,
        telegram_user_id=101, payload=update(1, text="/start"),
    )
    repo.claim_update(42, 1)
    repo.save_reply(42, 1, "Legacy reply")
    repo.save_update(
        bot_id=42, update_id=2, business_id="demo-studio", chat_id=-101,
        telegram_user_id=101, payload={"message": {"chat": {"type": "group"}}},
    )
    repo.claim_update(42, 2)
    repo.save_reply(42, 2, "Legacy group reply")
    reconcile_replies(url, bot)
    reconcile_replies(url, bot)
    outbox = TelegramOutbox(url)
    job = outbox.claim()
    assert job["message"] == "Legacy reply"
    assert outbox.claim() is None
    with connect(url) as connection:
        assert connection.execute("SELECT count(*) AS total FROM telegram_outbox").fetchone()["total"] == 1


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
        process_turn(turns, turns.claim(), runtime)
        process_turn(turns, turns.claim(), runtime)
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
    turns.finish(turns.claim(), "Available times", "published")
    save_update(url, bot, update(19, date=1999, text="1"))
    ready_updates(url)
    late = turns.claim()
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
