from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlparse

import pytest

from app.database import connect
from app.repositories import PostgresRepository
from app.scheduling import AvailabilityService
from app.booking import BookingService
from app.seed import seed


TOKEN_SECRET = "integration-test-secret-32-bytes-minimum!!"


@pytest.fixture
def scheduling_db(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL integration tests")
    database_name = urlparse(url).path.rstrip("/").split("/")[-1]
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a disposable database whose name ends in _test")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("BOOKING_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.setenv("DEMO_BUSINESS_ID", "demo-studio")
    monkeypatch.setenv("LLM_PROVIDER", "deterministic")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TRACING_ENABLED", "false")

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
        "bookings": BookingService(repository, TOKEN_SECRET),
    }

    with connect(url) as connection:
        connection.execute("TRUNCATE TABLE businesses CASCADE")
