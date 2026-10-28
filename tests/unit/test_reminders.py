from contextlib import contextmanager

import app.reminders as reminders_module
from app.reminders import ReminderWorker


class EmptyClaimResult:
    def fetchall(self):
        return []


class Connection:
    def __init__(self):
        self.query = ""
        self.params = None

    def execute(self, query, params):
        self.query = " ".join(query.split())
        self.params = params
        return EmptyClaimResult()


def test_reminder_claim_limits_rows_before_skip_locked(monkeypatch):
    connection = Connection()

    @contextmanager
    def fake_connect(_database_url):
        yield connection

    monkeypatch.setattr(reminders_module, "connect", fake_connect)

    claimed = ReminderWorker("postgresql://unused").deliver_due(limit=7)

    assert claimed == 0
    assert "ORDER BY send_at LIMIT %s FOR UPDATE SKIP LOCKED" in connection.query
    assert connection.params == (7,)
