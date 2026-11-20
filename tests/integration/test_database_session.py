from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.channels.telegram_repository import TelegramRepository
from app.database import _engine, database_session


@pytest.mark.parametrize("pool_size", ["0", "1"])
def test_session_hides_bound_values_and_recovers_after_rollback(scheduling_db, monkeypatch, pool_size):
    monkeypatch.setenv("CAPY_DB_POOL_MAX", pool_size)
    url = scheduling_db["url"].replace("postgresql://", "postgresql+psycopg://", 1)
    with pytest.raises(DBAPIError) as failure:
        with database_session(url) as session:
            session.execute(text("SELECT length(:value) / 0"), {"value": "private-value"})
    assert "private-value" not in str(failure.value)
    assert scheduling_db["repository"].service("demo-studio", "service-demo").active


def test_registration_lock_releases_when_closed(scheduling_db, monkeypatch):
    monkeypatch.setenv("CAPY_DB_POOL_MAX", "1")
    repository = TelegramRepository(scheduling_db["url"])
    lock = repository.acquire_worker_lock(55)
    assert lock is not None
    try:
        assert repository.acquire_worker_lock(55) is None
    finally:
        lock.close()
    lock = repository.acquire_worker_lock(55)
    assert lock is not None
    lock.close()


def test_availability_lookup_uses_the_same_calendar(scheduling_db):
    engine = _engine(scheduling_db["url"])
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    repository = scheduling_db["repository"]
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=2)
    event.listen(engine, "before_cursor_execute", record)
    try:
        _, _, one = repository.scheduling_data(
            "demo-studio", "service-demo", "professional-demo-ana", start, end,
        )
        one_query_count = len(statements)
        statements.clear()
        _, _, many = repository.scheduling_data("demo-studio", "service-demo", None, start, end)
        assert len(statements) == one_query_count == 6
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(one) == 1
    assert len(many) == 1
