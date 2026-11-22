import asyncio
import logging
import time
from contextlib import asynccontextmanager
from threading import Event, Lock, get_ident

import pytest

from app.channels.telegram_queue import ChatReady, TelegramQueue
from app.channels.telegram_turns import TelegramTurns
from app.telegram_worker import _keep_turn_claim_alive, process_pending
from tests.integration.helpers import connect
from tests.integration.test_telegram_production import ready_updates, register, update
from app.channels.telegram_intake import save_update

LOGGER = logging.getLogger(__name__)


async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@asynccontextmanager
async def processors(url, rabbit_url, *, count=1, interval=0.1):
    stop = Event()
    queues = [TelegramQueue(rabbit_url, 42, LOGGER, consume=True) for _ in range(count)]
    tasks = []
    started = []
    try:
        for queue in queues:
            queue.start()
        await until(lambda: all(queue.connected for queue in queues))
        for queue in queues:
            turns = TelegramTurns(url, register(url))
            ready = Event()
            started.append(ready)
            original_claim = turns.claim_next

            def claim_next(original_claim=original_claim, ready=ready):
                claim = original_claim()
                ready.set()
                return claim

            turns.claim_next = claim_next
            tasks.append(asyncio.create_task(process_pending(
                turns, None, queue, stop, LOGGER, recovery_interval=interval,
            )))
        await until(lambda: all(ready.is_set() for ready in started))
        yield queues, tasks, stop
    finally:
        stop.set()
        await asyncio.gather(*tasks)
        for queue in queues:
            await queue.close()


@pytest.mark.asyncio
async def test_five_processors_serialize_each_chat_and_sender_progresses(scheduling_db, rabbit_url, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.channels.telegram import TelegramBotApi
    from app.config import load_settings
    from app.telegram_sender import run_sender

    url = scheduling_db["url"]
    bot = register(url)
    entered, release, sent = set(), Event(), Event()
    active, maximum, per_thread = {}, {}, {}
    lock = Lock()

    def turn(turns, claim, _runtime):
        chat = claim.row["chat_id"]
        thread = get_ident()
        with lock:
            active[chat] = active.get(chat, 0) + 1
            maximum[chat] = max(maximum.get(chat, 0), active[chat])
            per_thread[thread] = per_thread.get(thread, 0) + 1
            assert per_thread[thread] == 1
            entered.add(claim.row["update_id"])
        try:
            with _keep_turn_claim_alive(turns, claim):
                assert release.wait(8)
                turns.finish(claim, "Saved response", None)
        finally:
            with lock:
                active[chat] -= 1
                per_thread[thread] -= 1

    monkeypatch.setattr("app.telegram_worker.process_turn", turn)
    monkeypatch.setenv("TELEGRAM_BOT_ID", "42")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:synthetic")
    monkeypatch.setattr(TelegramBotApi, "send_message", lambda *_: sent.set())
    save_update(url, bot, update(99, chat=999, text="saved reply"))
    ready_updates(url)
    turns = TelegramTurns(url, bot)
    turns.finish(turns.claim_chat(42, 999).claim, "Already ready", None)
    for i in range(1, 6):
        save_update(url, bot, update(i, chat=100 + i, text="slow turn"))
    save_update(url, bot, update(6, chat=101, text="follow-up"))
    ready_updates(url)
    async with processors(url, rabbit_url, count=5) as (queues, tasks, stop):
        try:
            await until(lambda: len(entered) == 5)
            assert 6 not in entered
            assert len(per_thread) == 5
            assert await queues[0].try_publish(ChatReady(42, 101))
            with ThreadPoolExecutor(max_workers=1) as executor:
                sender = executor.submit(run_sender, load_settings(), stop, LOGGER)
                try:
                    await until(sent.is_set)
                    assert not release.is_set()
                finally:
                    stop.set()
                    sender.result(timeout=3)
            stop.clear()
        finally:
            release.set()
        await until(lambda: 6 in entered)
        assert all(value == 1 for value in maximum.values())


@pytest.mark.asyncio
async def test_early_deferred_busy_and_duplicate_hints_leave_durable_work(scheduling_db, rabbit_url, monkeypatch):
    url = scheduling_db["url"]
    bot = register(url)
    finished = []

    def turn(turns, claim, _):
        with connect(url) as connection:
            assert connection.execute(
                "SELECT ready_at <= clock_timestamp() AS ready FROM telegram_inbox WHERE update_id=%s",
                (claim.row["update_id"],),
            ).fetchone()["ready"]
        turns.finish(claim, "Done", None)
        finished.append(claim.row["update_id"])

    monkeypatch.setattr("app.telegram_worker.process_turn", turn)
    async with processors(url, rabbit_url) as (queues, _, _):
        queue = queues[0]
        result = save_update(url, bot, update(1, text="early"))
        assert result.notification == ChatReady(42, 101)
        assert await queue.try_publish(result.notification)
        await until(lambda: finished == [1])
        for _ in range(3):
            assert await queue.try_publish(result.notification)
        save_update(url, bot, update(2, chat=102, text="deferred"))
        with connect(url) as connection:
            connection.execute("UPDATE telegram_inbox SET ready_at=now()+interval '1 minute' WHERE update_id=2")
        assert await queue.try_publish(ChatReady(42, 102))
        save_update(url, bot, update(3, chat=103, text="busy"))
        with connect(url) as connection:
            connection.execute("UPDATE telegram_inbox SET ready_at=now() WHERE update_id=3")
        turns = TelegramTurns(url, bot)
        busy = turns.claim_chat(42, 103).claim
        assert await queue.try_publish(ChatReady(42, 103))
        save_update(url, bot, update(4, chat=104, text="other chat"))
        with connect(url) as connection:
            connection.execute("UPDATE telegram_inbox SET ready_at=now() WHERE update_id=4")
        assert await queue.try_publish(ChatReady(42, 104))
        await until(lambda: 4 in finished)
        assert 2 not in finished
        assert 3 not in finished
        turns.defer_before_execution(busy, "test_dependency")
        with connect(url) as connection:
            assert connection.execute("SELECT status FROM telegram_inbox WHERE update_id=3").fetchone()["status"] == "received"
        ready_updates(url)
        await until(lambda: {1, 2, 3, 4} == set(finished))
    with connect(url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM telegram_outbox").fetchone()["n"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("backlog", [False, True])
async def test_recovery_and_notifications_cannot_starve_each_other(scheduling_db, rabbit_url, monkeypatch, backlog):
    url = scheduling_db["url"]
    bot = register(url)
    finished = []

    def turn(turns, claim, _):
        time.sleep(0.04)
        turns.finish(claim, None, None)
        finished.append(claim.row["update_id"])

    monkeypatch.setattr("app.telegram_worker.process_turn", turn)
    if backlog:
        for i in range(1, 21):
            save_update(url, bot, update(i, chat=100 + i, text="backlog"))
        ready_updates(url)
    async with processors(url, rabbit_url, interval=0.03) as (queues, _, _):
        queue = queues[0]
        save_update(url, bot, update(99, chat=999, text="target"))
        ready_updates(url)
        if backlog:
            assert await queue.try_publish(ChatReady(42, 999))
            await until(lambda: 99 in finished)
            assert finished.index(99) < 5
        else:
            async def flood():
                while 99 not in finished:
                    await queue.try_publish(ChatReady(42, 777))
                    await asyncio.sleep(0)
            await asyncio.wait_for(flood(), 3)


@pytest.mark.asyncio
async def test_reconnect_during_slow_turn_preserves_slot_lease_and_idempotency(scheduling_db, rabbit_url, monkeypatch):
    url = scheduling_db["url"]
    entered, release = Event(), Event()
    calls = []
    monkeypatch.setattr("app.telegram_worker.LEASE_SECONDS", 0.3)

    def turn(turns, claim, _):
        calls.append(claim.row["update_id"])
        with _keep_turn_claim_alive(turns, claim):
            entered.set()
            assert release.wait(8)
            turns.finish(claim, "Exactly one saved reply", "checkpoint")

    monkeypatch.setattr("app.telegram_worker.process_turn", turn)
    async with processors(url, rabbit_url, interval=30) as (queues, tasks, stop):
        queue = queues[0]
        result = save_update(url, register(url), update(1, text="slow"))
        ready_updates(url)
        assert await queue.try_publish(result.notification)
        try:
            await until(entered.is_set)
            old_channel = queue._channel
            with connect(url) as connection:
                old_lease = connection.execute("SELECT lease_until FROM telegram_chat_processing_state").fetchone()["lease_until"]
            await old_channel.close()
            await until(lambda: queue.connected and queue._channel is not old_channel)
            for _ in range(3):
                assert await queue.try_publish(ChatReady(42, 101))
                await asyncio.sleep(1)
            assert calls == [1]
            with connect(url) as connection:
                assert connection.execute("SELECT lease_until FROM telegram_chat_processing_state").fetchone()["lease_until"] > old_lease
            tasks[0].cancel()
            await asyncio.sleep(0.05)
            assert not tasks[0].done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        tasks.clear()
        stop.clear()
        fresh = asyncio.create_task(process_pending(TelegramTurns(url, register(url)), None, queue, stop, LOGGER, recovery_interval=0.05))
        tasks.append(fresh)
        assert await queue.try_publish(ChatReady(42, 101))
        await until(lambda: queue._deliveries.empty())
        assert calls == [1]
        with connect(url) as connection:
            assert connection.execute("SELECT count(*) AS n FROM telegram_outbox").fetchone()["n"] == 1
