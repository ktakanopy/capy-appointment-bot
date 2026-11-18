"""Record when RabbitMQ should wake a Telegram worker."""

from __future__ import annotations

from datetime import datetime

from app.database import connect


def schedule_publication(
    connection, *, bot_id: int | None = None, chat_id: int | None = None,
    outgoing_id: int | None = None, due_at: datetime | None = None, delay: float = 0,
) -> None:
    """Persist a wake-up in the caller's transaction and keep the earliest pending one."""
    connection.execute(
        """INSERT INTO telegram_publications (bot_id,chat_id,outgoing_id,due_at)
           VALUES (%s,%s,%s,COALESCE(%s,clock_timestamp()+%s*interval '1 second'))
           ON CONFLICT (bot_id,chat_id,outgoing_id) DO UPDATE
           SET due_at=CASE WHEN telegram_publications.claimed_until IS NULL
                           THEN LEAST(telegram_publications.due_at,EXCLUDED.due_at)
                           ELSE EXCLUDED.due_at END,
               claimed_until=NULL""",
        (bot_id, chat_id, outgoing_id, due_at, delay),
    )


def publish_due(database_url: str, publish, role: str | None = None) -> bool:
    """Confirm one publication without holding a database lock across the broker call."""
    with connect(database_url) as connection:
        row = connection.execute(
            """UPDATE telegram_publications SET claimed_until=clock_timestamp()+interval '30 seconds'
               WHERE id=(
                   SELECT id FROM telegram_publications WHERE due_at<=statement_timestamp()
                     AND (claimed_until IS NULL OR claimed_until<=clock_timestamp())
                     AND (%s::text IS NULL OR (outgoing_id IS NULL AND %s='process')
                          OR (outgoing_id IS NOT NULL AND %s='send'))
                   ORDER BY due_at,id LIMIT 1 FOR UPDATE SKIP LOCKED
               )
               RETURNING id,bot_id,chat_id,outgoing_id,claimed_until""",
            (role, role, role),
        ).fetchone()
    if row is None:
        return False
    publish(row)
    with connect(database_url) as connection:
        connection.execute(
            "DELETE FROM telegram_publications WHERE id=%s AND claimed_until=%s",
            (row["id"], row["claimed_until"]),
        )
    return True


INCOMING_BACKFILL = """
    SELECT c.bot_id,c.chat_id,greatest(min(u.ready_at),coalesce(c.lease_until,'-infinity')) AS due_at
    FROM telegram_updates u JOIN telegram_conversations c USING (bot_id,chat_id)
    WHERE u.status IN ('received','processing')
      AND NOT EXISTS (
          SELECT 1 FROM telegram_publications p WHERE p.bot_id=c.bot_id AND p.chat_id=c.chat_id
      )
    GROUP BY c.bot_id,c.chat_id ORDER BY c.bot_id,c.chat_id LIMIT 500
"""
OUTGOING_BACKFILL = """
    SELECT o.id AS outgoing_id,o.ready_at AS due_at FROM telegram_outbox o
    WHERE o.status='ready' AND NOT EXISTS (
        SELECT 1 FROM telegram_publications p WHERE p.outgoing_id=o.id
    )
    ORDER BY o.id LIMIT 500
"""


def _backfill(database_url: str, candidates: str, columns: str) -> int:
    """Commit in batches so a migration can resume after interruption."""
    total = 0
    while True:
        with connect(database_url) as connection:
            result = connection.execute(
                f"""WITH batch AS ({candidates}), inserted AS (
                        INSERT INTO telegram_publications({columns})
                        SELECT {columns} FROM batch ON CONFLICT DO NOTHING RETURNING id
                    )
                    SELECT (SELECT count(*) FROM batch) AS attempted,
                           (SELECT count(*) FROM inserted) AS inserted"""
            ).fetchone()
        total += result["inserted"]
        if result["attempted"] < 500:
            return total


def backfill_publications(database_url: str, role: str | None = None) -> tuple[int, int]:
    """Recreate missing wake-ups while the corresponding relay route is paused."""
    incoming = outgoing = 0
    if role in (None, "process"):
        incoming = _backfill(database_url, INCOMING_BACKFILL, "bot_id,chat_id,due_at")
    if role in (None, "send"):
        outgoing = _backfill(database_url, OUTGOING_BACKFILL, "outgoing_id,due_at")
    return incoming, outgoing
