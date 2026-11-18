"""Exercise RabbitMQ transport and its PostgreSQL recovery boundaries."""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import urlsplit

import pika
import pytest

from app.channels.telegram_broker import (
    DEAD_LETTER_QUEUE,
    EXCHANGE,
    OUTGOING_QUEUE,
    broker_channel,
    chat_partition,
    consume_wakeups,
    incoming_queue,
    publish_wakeup,
)
from app.channels.telegram_delivery import TelegramOutbox
from app.channels.telegram_intake import save_update
from app.channels.telegram_publications import backfill_publications, publish_due, schedule_publication
from app.channels.telegram_turns import ClaimLost, TelegramTurns
from app.database import database_session
from tests.integration.helpers import connect
from tests.integration.test_telegram_production import register, update

PARTITIONS = 2
LOGGER = logging.getLogger(__name__)


@pytest.fixture
def broker_url():
    """Require a disposable vhost before purging queues shared within the test run."""
    url = os.getenv("TEST_AMQP_URL")
    if not url and os.getenv("CAPY_REQUIRE_INTEGRATION") == "1":
        pytest.fail("CI requires TEST_AMQP_URL")
    if not url:
        pytest.skip("set TEST_AMQP_URL to run RabbitMQ integration tests")
    assert urlsplit(url).path.endswith("_test")
    with broker_channel(url, PARTITIONS) as (_, channel):
        for queue in (OUTGOING_QUEUE, DEAD_LETTER_QUEUE, *(incoming_queue(i) for i in range(PARTITIONS))):
            channel.queue_purge(queue)
    return url


def wait_for(predicate, timeout=8):
    """Poll a durable outcome while the worker runs in another thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    pytest.fail("Timed out waiting for Telegram worker")


def test_broker_round_trip_orders_chats_and_deduplicates_sends(scheduling_db, broker_url):
    url = scheduling_db["url"]
    bot = register(url)
    voice_chat = 101
    voice_partition = chat_partition(bot.bot_id, voice_chat, PARTITIONS)
    other_chat = next(
        chat for chat in range(102, 200)
        if chat_partition(bot.bot_id, chat, PARTITIONS) != voice_partition
    )
    turns, outbox = TelegramTurns(url), TelegramOutbox(url)
    voice_started, release_voice, stop = Event(), Event(), Event()
    observed = []
    sent = []

    def process(bot_id, chat_id):
        """Stub inference while exercising the real scoped turn transaction."""
        claim = turns.claim_chat(bot_id, chat_id)
        if claim is None:
            return
        number = claim.row["update_id"]
        if number == 1:
            voice_started.set()
            assert release_voice.wait(15)
        if number == 2:
            assert claim.checkpoint_thread == "voice-checkpoint"
        observed.append(number)
        turns.finish(claim, "Synthetic reply", "voice-checkpoint")

    def send(outgoing_id):
        """Record the send from the same reservation used by the Telegram sender."""
        job = outbox.claim_by_id(outgoing_id)
        if job:
            assert outbox.finish(job, "sent")
            sent.append(job["update_id"])

    assert save_update(url, bot, update(2, chat=voice_chat, text="1"))
    assert save_update(url, bot, update(1, chat=voice_chat, voice={"file_id": "clip"}))
    assert save_update(url, bot, update(3, chat=other_chat, text="prices"))
    assert save_update(url, bot, update(2, chat=voice_chat, text="duplicate"))
    assert backfill_publications(url) == (0, 0)

    with ThreadPoolExecutor(max_workers=3) as workers, broker_channel(broker_url, PARTITIONS) as (_, channel):
        channel.confirm_delivery()
        consumers = [
            workers.submit(consume_wakeups, broker_url, PARTITIONS, incoming_queue(i), 2, process, stop, LOGGER)
            for i in range(PARTITIONS)
        ]
        consumers.append(workers.submit(
            consume_wakeups, broker_url, PARTITIONS, OUTGOING_QUEUE, 1, send, stop, LOGGER,
        ))

        def pump():
            """Publish one due wake-up, deliberately duplicating it to model a lost confirmation."""
            def duplicate(row):
                """Deliver the same durable identifiers twice."""
                publish_wakeup(channel, row, PARTITIONS)
                publish_wakeup(channel, row, PARTITIONS)

            return publish_due(url, duplicate)

        try:
            def other_chat_finished():
                """Confirm another partition advances while the voice partition is busy."""
                pump()
                return voice_started.is_set() and observed == [3]

            wait_for(other_chat_finished)
            release_voice.set()

            def all_sent():
                """Flush subsequent turn and send publications as they become due."""
                pump()
                return len(sent) == 3

            wait_for(all_sent)
            assert observed == [3, 1, 2]
            assert sent.index(1) < sent.index(2)
            channel.basic_publish(EXCHANGE, OUTGOING_QUEUE, b"invalid", mandatory=True)
            wait_for(lambda: channel.queue_declare(DEAD_LETTER_QUEUE, passive=True).method.message_count == 1)
            with connect(url) as connection:
                assert connection.execute(
                    "SELECT count(*) AS n FROM telegram_outbox WHERE status='sent'"
                ).fetchone()["n"] == 3
        finally:
            release_voice.set()
            stop.set()
        for future in consumers:
            future.result(timeout=3)


def test_unconfirmed_publication_survives_and_expired_workers_cannot_finish(scheduling_db, broker_url):
    url = scheduling_db["url"]
    bot = register(url)
    assert save_update(url, bot, update(1, text="book"))
    with connect(url) as connection:
        connection.execute("UPDATE telegram_publications SET due_at=now()")
        connection.execute("UPDATE telegram_updates SET ready_at=now()")

    with broker_channel(broker_url, PARTITIONS) as (_, channel):
        channel.confirm_delivery()
        with pytest.raises(pika.exceptions.UnroutableError):
            publish_due(url, lambda row: channel.basic_publish(EXCHANGE, "absent", b"[]", mandatory=True))
        with connect(url) as connection:
            connection.execute("UPDATE telegram_publications SET claimed_until=now()")
        assert publish_due(url, lambda row: publish_wakeup(channel, row, PARTITIONS))
        assert not publish_due(url, lambda row: publish_wakeup(channel, row, PARTITIONS))

    turns = TelegramTurns(url)
    original = turns.claim_chat(bot.bot_id, 101)
    assert turns.claim_chat(bot.bot_id, 101) is None
    with connect(url) as connection:
        connection.execute("UPDATE telegram_conversations SET lease_until=now()")
    recovered = turns.claim_chat(bot.bot_id, 101)
    assert recovered.interrupted and recovered.generation > original.generation
    with pytest.raises(ClaimLost):
        turns.finish(original, "Stale reply", "stale-draft")
    turns.finish(recovered, "Unknown outcome", None)
    outbox = TelegramOutbox(url)
    with connect(url) as connection:
        outgoing_id = connection.execute("SELECT id FROM telegram_outbox").fetchone()["id"]
    assert outbox.claim_by_id(outgoing_id)
    assert outbox.claim_by_id(outgoing_id) is None
    with connect(url) as connection:
        connection.execute("UPDATE telegram_outbox SET lease_until=now()")
    assert outbox.recover_expired() == 1
    assert outbox.claim_by_id(outgoing_id) is None


def test_relay_preserves_replacement_wakeup_written_during_confirmation(scheduling_db):
    url = scheduling_db["url"]
    bot = register(url)
    assert save_update(url, bot, update(1, text="book"))
    with connect(url) as connection:
        connection.execute("DELETE FROM telegram_publications")
        connection.execute("UPDATE telegram_updates SET ready_at=now()")
    assert backfill_publications(url, "process") == (1, 0)
    assert backfill_publications(url, "process") == (0, 0)

    def replace_publication(row):
        """Simulate a worker deferring the same chat while the broker confirms."""
        with database_session(url) as session:
            schedule_publication(session, bot_id=row["bot_id"], chat_id=row["chat_id"], delay=60)

    assert publish_due(url, replace_publication, "process")
    assert not publish_due(url, replace_publication, "process")
    with connect(url) as connection:
        pending = connection.execute("SELECT claimed_until FROM telegram_publications").fetchall()
    assert pending == [{"claimed_until": None}]
