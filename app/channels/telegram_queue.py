from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from app.observability import log_event

QUEUE_NAME = "telegram.chat_ready"


@dataclass(frozen=True)
class ChatReady:
    bot_id: int
    chat_id: int

    def __post_init__(self):
        if any(type(value) is not int or not 0 < value < 2**63
               for value in (self.bot_id, self.chat_id)):
            raise ValueError("invalid_identifiers")

    @classmethod
    def decode(cls, body: bytes, bot_id: int) -> ChatReady:
        if len(body) > 256:
            raise ValueError("oversized")
        value = json.loads(body)
        if not isinstance(value, dict) or set(value) != {"bot_id", "chat_id"}:
            raise ValueError("malformed")
        notification = cls(**value)
        if notification.bot_id != bot_id:
            raise ValueError("out_of_scope")
        return notification

    def encode(self) -> bytes:
        return json.dumps({"bot_id": self.bot_id, "chat_id": self.chat_id}).encode()


@dataclass(frozen=True)
class ChatDelivery:
    notification: ChatReady
    message: AbstractIncomingMessage


class TelegramQueue:
    def __init__(self, url: str, bot_id: int, logger, *, consume: bool = False):
        self._url = url
        self.bot_id = bot_id
        self.logger = logger
        self._consume = consume
        self._ready = asyncio.Event()
        self._deliveries: asyncio.Queue[ChatDelivery] = asyncio.Queue(maxsize=1)
        self._channel = None
        self._runner: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._ready.is_set()

    def start(self) -> None:
        if self._runner is None:
            self._runner = asyncio.create_task(self._connect_loop())

    async def close(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
            self._runner = None

    async def _connect_loop(self) -> None:
        backoff = 0.25
        while True:
            connection = None
            try:
                async with asyncio.timeout(1):
                    connection = await aio_pika.connect(self._url, timeout=1)
                    channel = await connection.channel(
                        publisher_confirms=True, on_return_raises=True,
                    )
                    await channel.set_qos(prefetch_count=1)
                    queue = await channel.declare_queue(
                        QUEUE_NAME, durable=True, exclusive=False, auto_delete=False,
                    )
                    lost = asyncio.Event()
                    connection.close_callbacks.add(lambda *_: lost.set())
                    channel.close_callbacks.add(lambda *_: lost.set())
                    if self._consume:
                        await queue.consume(self._receive, no_ack=False)
                self._channel = channel
                self._ready.set()
                backoff = 0.25
                log_event(self.logger, "telegram.broker.connected", consumer=self._consume)
                await lost.wait()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log_event(self.logger, "telegram.broker.unavailable", failure_type=type(error).__name__)
            finally:
                self._ready.clear()
                self._channel = None
                if connection is not None:
                    try:
                        async with asyncio.timeout(2):
                            await connection.close()
                    except Exception:
                        pass
            log_event(self.logger, "telegram.broker.disconnected")
            await asyncio.sleep(backoff + random.uniform(0, backoff / 4))
            backoff = min(backoff * 2, 5)

    async def try_publish(self, notification: ChatReady, timeout: float = 1) -> bool:
        try:
            async with asyncio.timeout(timeout):
                if notification.bot_id != self.bot_id:
                    raise ValueError("out_of_scope")
                await self._ready.wait()
                channel = self._channel
                await channel.default_exchange.publish(
                    aio_pika.Message(
                        body=notification.encode(), content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key=QUEUE_NAME, mandatory=True, timeout=timeout,
                )
        except Exception as error:
            log_event(self.logger, "telegram.notification.publish_failed", failure_type=type(error).__name__)
            return False
        log_event(self.logger, "telegram.notification.published")
        return True

    async def _receive(self, message: AbstractIncomingMessage) -> None:
        try:
            notification = ChatReady.decode(message.body, self.bot_id)
        except (ValueError, TypeError, RecursionError) as error:
            reason = error.args[0] if error.args and error.args[0] in {
                "oversized", "out_of_scope", "invalid_identifiers",
            } else "malformed"
            log_event(self.logger, "telegram.notification.rejected", reason=reason)
            await self._settle(message, reject=True)
            return
        try:
            self._deliveries.put_nowait(ChatDelivery(notification, message))
        except asyncio.QueueFull:
            # Reconnection can redeliver while an old channel's hint is still buffered.
            log_event(self.logger, "telegram.notification.rejected", reason="buffer_full")
            await self._settle(message, reject=True)

    async def next_delivery(self, timeout: float) -> ChatDelivery | None:
        try:
            return await asyncio.wait_for(self._deliveries.get(), timeout)
        except TimeoutError:
            return None

    async def acknowledge(self, delivery: ChatDelivery) -> bool:
        return await self._settle(delivery.message)

    async def _settle(self, message: AbstractIncomingMessage, *, reject: bool = False) -> bool:
        try:
            async with asyncio.timeout(1):
                if reject:
                    await message.reject(requeue=False)
                else:
                    await message.ack()
            return True
        except Exception as error:
            # Delivery tags belong to the original channel, including after reconnection.
            log_event(self.logger, "telegram.notification.settlement_lost", failure_type=type(error).__name__)
            return False
