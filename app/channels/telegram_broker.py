"""Transport Telegram wake-ups over RabbitMQ without exposing message content."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pika

from app.channels.telegram_publications import publish_due
from app.database import DATABASE_ERRORS
from app.observability import log_event

EXCHANGE = "capy.telegram.v1"
OUTGOING_QUEUE = f"{EXCHANGE}.outgoing"
INCOMING_QUEUE = f"{EXCHANGE}.incoming.0"
DEAD_LETTER_QUEUE = f"{EXCHANGE}.dead"
HANDLER_ATTEMPTS = 5


@contextmanager
def broker_channel(url: str):
    """Open a Pika channel and declare the shared durable routing layout."""
    parameters = pika.URLParameters(url)
    parameters.heartbeat = 30
    parameters.socket_timeout = 5
    parameters.stack_timeout = 10
    parameters.blocked_connection_timeout = 10
    with pika.BlockingConnection(parameters) as connection:
        channel = connection.channel()
        channel.exchange_declare(EXCHANGE, exchange_type="direct", durable=True)
        channel.queue_declare(DEAD_LETTER_QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
        channel.queue_bind(DEAD_LETTER_QUEUE, EXCHANGE, DEAD_LETTER_QUEUE)
        options = {
            "x-queue-type": "quorum",
            "x-delivery-limit": -1,
            "x-dead-letter-exchange": EXCHANGE,
            "x-dead-letter-routing-key": DEAD_LETTER_QUEUE,
        }
        for queue, arguments in (
            (OUTGOING_QUEUE, options),
            (INCOMING_QUEUE, {**options, "x-single-active-consumer": True}),
        ):
            channel.queue_declare(queue, durable=True, arguments=arguments)
            channel.queue_bind(queue, EXCHANGE, queue)
        yield connection, channel


def publish_wakeup(channel, row: dict) -> None:
    """Publish only IDs and require a broker confirmation on a valid route."""
    if row["outgoing_id"] is None:
        bot_id, chat_id = row["bot_id"], row["chat_id"]
        queue = INCOMING_QUEUE
        ids = [bot_id, chat_id]
    else:
        queue, ids = OUTGOING_QUEUE, [row["outgoing_id"]]
    channel.basic_publish(
        EXCHANGE, queue, json.dumps(ids).encode("ascii"), mandatory=True,
        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2,
                                        message_id=str(row["id"])),
    )


def relay_publications(url, database_url, stop, logger, maintenance, role=None) -> None:
    """Forward due records and reconnect without discarding unconfirmed work."""
    while not stop.is_set():
        try:
            with broker_channel(url) as (connection, channel):
                channel.confirm_delivery()
                while not stop.is_set():
                    maintenance()
                    if not publish_due(database_url, lambda row: publish_wakeup(channel, row), role):
                        connection.process_data_events(time_limit=0.05)
        except (pika.exceptions.AMQPError, *DATABASE_ERRORS, OSError) as error:
            log_event(logger, "telegram.relay.retry", failure_type=type(error).__name__)
            stop.wait(1)


def _read_ids(body: bytes, count: int) -> list[int]:
    """Validate the identifier-only job envelope before touching PostgreSQL."""
    if len(body) > 80:
        raise ValueError("Wake-up payload exceeds the identifier limit.")
    ids = json.loads(body)
    if (
        not isinstance(ids, list) or len(ids) != count
        or any(type(value) is not int or not 0 < value < 2**63 for value in ids)
    ):
        raise ValueError("Invalid Telegram wake-up identifiers.")
    return ids


def _handle_delivery(connection, channel, executor, delivery, handle, id_count, stop, logger) -> None:
    """Keep AMQP heartbeats running during inference and settle only finished work."""
    method, properties, body = delivery
    tag = method.delivery_tag
    try:
        ids = _read_ids(body, id_count)
    except ValueError:
        log_event(logger, "telegram.broker.rejected", reason="invalid_envelope")
        channel.basic_reject(tag, requeue=False)
        return
    def wake_io(_future) -> None:
        """Interrupt Pika's event wait as soon as the worker has a result."""
        try:
            connection.add_callback_threadsafe(lambda: None)
        except pika.exceptions.ConnectionWrongStateError:
            return

    future = executor.submit(handle, *ids)
    future.add_done_callback(wake_io)
    while not future.done():
        connection.process_data_events(time_limit=0.5)
    error = future.exception()
    if error is None:
        channel.basic_ack(tag)
        return
    attempts = 1 + (properties.headers or {}).get("x-delivery-count", 0)
    retry = isinstance(error, (*DATABASE_ERRORS, OSError)) or attempts < HANDLER_ATTEMPTS
    log_event(logger, "telegram.broker.handler_failed", failure_type=type(error).__name__, retry=retry)
    stop.wait(1)
    channel.basic_reject(tag, requeue=retry)


def consume_wakeups(url, queue, id_count, handle, stop, logger) -> None:
    """Handle one delivery at a time while RabbitMQ owns consumer failover."""
    while not stop.is_set():
        try:
            with broker_channel(url) as (connection, channel):
                channel.basic_qos(prefetch_count=1)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    for delivery in channel.consume(queue, inactivity_timeout=0.5):
                        if stop.is_set():
                            break
                        if delivery[0] is not None:
                            _handle_delivery(connection, channel, executor, delivery, handle,
                                             id_count, stop, logger)
                channel.cancel()
        except (pika.exceptions.AMQPError, OSError) as error:
            log_event(logger, "telegram.broker.reconnect", failure_type=type(error).__name__)
            stop.wait(1)
