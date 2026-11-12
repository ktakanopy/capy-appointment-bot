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


@pytest.fixture
def scheduling_db(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url and os.getenv("CAPY_REQUIRE_INTEGRATION") == "1":
        pytest.fail("CI requires TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL integration tests")
    database_name = urlparse(url).path.rstrip("/").split("/")[-1]
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a disposable database whose name ends in _test")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DEMO_BUSINESS_ID", "demo-studio")
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
        "bookings": BookingService(repository),
    }

    with connect(url) as connection:
        connection.execute("TRUNCATE TABLE businesses CASCADE")
