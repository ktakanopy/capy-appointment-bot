from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from urllib.parse import urlparse

import aio_pika
import pytest

from app.booking import BookingService
from app.repositories import PostgresRepository
from app.scheduling import AvailabilityService
from app.seed import seed
from tests.integration.helpers import connect


@pytest.fixture
def scheduling_db(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url and os.getenv("CAPY_REQUIRE_INTEGRATION") == "1":
        pytest.fail("CI requires TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL integration tests")
    database_name = urlparse(url).path.rstrip("/").split("/")[-1]
    if not database_name.endswith("_test"):
        pytest.fail(
            "TEST_DATABASE_URL must point to a disposable database whose name ends in _test"
        )

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DEMO_BUSINESS_ID", "demo-studio")
    monkeypatch.setenv("TRACING_ENABLED", "false")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://unused:unused@127.0.0.1:1/capy_test")
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS", "0.03")

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    with connect(url) as connection:
        connection.execute("TRUNCATE TABLE businesses CASCADE")
    seed()

    repository = PostgresRepository(url)
    yield {
        "url": url,
        "repository": repository,
        "availability": AvailabilityService(repository),
        "bookings": BookingService(repository),
    }

    with connect(url) as connection:
        connection.execute("TRUNCATE TABLE businesses CASCADE")


@pytest.fixture
def rabbit_url(monkeypatch):
    url = os.getenv("TEST_RABBITMQ_URL")
    if not url:
        if os.getenv("CAPY_REQUIRE_INTEGRATION") == "1":
            pytest.fail("CI requires TEST_RABBITMQ_URL")
        pytest.skip("set TEST_RABBITMQ_URL to a disposable vhost ending in _test")
    if not urlparse(url).path.endswith("_test"):
        pytest.fail("TEST_RABBITMQ_URL must use a disposable vhost ending in _test")

    async def purge():
        connection = await aio_pika.connect(url, timeout=3)
        async with connection:
            channel = await connection.channel()
            queue = await channel.declare_queue("telegram.chat_ready", durable=True)
            await queue.purge()

    asyncio.run(purge())
    monkeypatch.setenv("RABBITMQ_URL", url)
    yield url
    asyncio.run(purge())
