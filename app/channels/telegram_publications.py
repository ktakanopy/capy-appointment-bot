"""Record when RabbitMQ should wake a Telegram worker."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database import database_session
from app.db_models import TelegramConversation, TelegramOutboxRow, TelegramPublication, TelegramUpdate

BACKFILL_BATCH_SIZE = 500


def schedule_publication(
    session: Session, *, bot_id: int | None = None, chat_id: int | None = None,
    outgoing_id: int | None = None, due_at: datetime | None = None, delay: float = 0,
) -> None:
    """Persist a wake-up in the caller's transaction and keep the earliest pending one."""
    publication = insert(TelegramPublication).values(
        bot_id=bot_id, chat_id=chat_id, outgoing_id=outgoing_id,
        due_at=due_at if due_at is not None else func.clock_timestamp() + timedelta(seconds=delay),
    )
    session.execute(publication.on_conflict_do_update(
        index_elements=["bot_id", "chat_id", "outgoing_id"],
        set_={
            "due_at": case(
                (TelegramPublication.claimed_until.is_(None),
                 func.least(TelegramPublication.due_at, publication.excluded.due_at)),
                else_=publication.excluded.due_at,
            ),
            "claimed_until": None,
        },
    ))


def publish_due(database_url: str, publish, role: str | None = None) -> bool:
    """Confirm one publication without holding a database lock across the broker call."""
    candidate = select(TelegramPublication.id).where(
        TelegramPublication.due_at <= func.statement_timestamp(),
        or_(TelegramPublication.claimed_until.is_(None),
            TelegramPublication.claimed_until <= func.clock_timestamp()),
    )
    if role == "process":
        candidate = candidate.where(TelegramPublication.outgoing_id.is_(None))
    elif role == "send":
        candidate = candidate.where(TelegramPublication.outgoing_id.is_not(None))
    elif role is not None:
        return False
    candidate = candidate.order_by(TelegramPublication.due_at, TelegramPublication.id).limit(1)
    candidate = candidate.with_for_update(skip_locked=True)
    statement = update(TelegramPublication).where(
        TelegramPublication.id == candidate.scalar_subquery(),
    ).values(claimed_until=func.clock_timestamp() + timedelta(seconds=30)).returning(
        TelegramPublication.id, TelegramPublication.bot_id, TelegramPublication.chat_id,
        TelegramPublication.outgoing_id, TelegramPublication.claimed_until,
    )
    with database_session(database_url) as session:
        row = session.execute(statement).mappings().first()
    if row is None:
        return False
    publish(row)
    with database_session(database_url) as session:
        session.execute(delete(TelegramPublication).where(
            TelegramPublication.id == row["id"],
            TelegramPublication.claimed_until == row["claimed_until"],
        ))
    return True


def _incoming_backfill():
    already_scheduled = select(TelegramPublication.id).where(
        TelegramPublication.bot_id == TelegramConversation.bot_id,
        TelegramPublication.chat_id == TelegramConversation.chat_id,
    ).exists()
    return select(
        TelegramConversation.bot_id, TelegramConversation.chat_id,
        func.greatest(func.min(TelegramUpdate.ready_at), TelegramConversation.lease_until).label("due_at"),
    ).join(
        TelegramUpdate, (TelegramUpdate.bot_id == TelegramConversation.bot_id)
        & (TelegramUpdate.chat_id == TelegramConversation.chat_id),
    ).where(
        TelegramUpdate.status.in_(("received", "processing")), ~already_scheduled,
    ).group_by(TelegramConversation.bot_id, TelegramConversation.chat_id).order_by(
        TelegramConversation.bot_id, TelegramConversation.chat_id,
    )


def _outgoing_backfill():
    already_scheduled = select(TelegramPublication.id).where(
        TelegramPublication.outgoing_id == TelegramOutboxRow.id,
    ).exists()
    return select(
        TelegramOutboxRow.id.label("outgoing_id"), TelegramOutboxRow.ready_at.label("due_at"),
    ).where(TelegramOutboxRow.status == "ready", ~already_scheduled).order_by(TelegramOutboxRow.id)


def _backfill(database_url: str, candidates) -> int:
    """Commit in batches so a migration can resume after interruption."""
    batch = candidates.limit(BACKFILL_BATCH_SIZE).cte("batch")
    inserted = insert(TelegramPublication).from_select(
        list(batch.c.keys()), select(batch),
    ).on_conflict_do_nothing().returning(TelegramPublication.id).cte("inserted")
    counts = select(
        select(func.count()).select_from(batch).scalar_subquery(),
        select(func.count()).select_from(inserted).scalar_subquery(),
    )
    total = 0
    while True:
        with database_session(database_url) as session:
            attempted, inserted_count = session.execute(counts).one()
        total += inserted_count
        if attempted < BACKFILL_BATCH_SIZE:
            return total


def backfill_publications(database_url: str, role: str | None = None) -> tuple[int, int]:
    """Recreate missing wake-ups while the corresponding relay route is paused."""
    incoming = outgoing = 0
    if role in (None, "process"):
        incoming = _backfill(database_url, _incoming_backfill())
    if role in (None, "send"):
        outgoing = _backfill(database_url, _outgoing_backfill())
    return incoming, outgoing
