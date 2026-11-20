import re
from contextlib import contextmanager

from sqlalchemy.dialects.postgresql import dialect

import app.reminders as reminders_module
from app.reminders import ReminderWorker


class EmptyClaimResult:
    def all(self):
        return []


class Session:
    def __init__(self):
        self.query = ""
        self.params = None

    def scalars(self, statement):
        compiled = statement.compile(dialect=dialect())
        self.query = " ".join(str(compiled).split())
        self.params = compiled.params
        return EmptyClaimResult()


def test_reminder_claim_limits_rows_before_skip_locked(monkeypatch):
    session = Session()

    @contextmanager
    def fake_session(_database_url):
        yield session

    monkeypatch.setattr(reminders_module, "database_session", fake_session)

    claimed = ReminderWorker("postgresql://unused").deliver_due(limit=7)

    assert claimed == 0
    assert re.search(r"ORDER BY reminders.send_at LIMIT %\(param_1\)s(?:::INTEGER)? FOR UPDATE SKIP LOCKED", session.query)
    assert session.params["param_1"] == 7
