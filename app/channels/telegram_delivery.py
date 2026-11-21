"""Claim and settle pending Telegram replies in PostgreSQL."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased

from app.channels.bots import TelegramBot
from app.database import database_session
from app.db_models import TelegramOutboxRow, TelegramUpdate

SEND_LEASE_SECONDS = 45
RATE_LIMIT_ERRORS = ("telegram_api_429", "telegram_http_429")


class TelegramOutbox:
    def __init__(self, database_url: str, bot: TelegramBot):
        self.database_url = database_url
        self.bot = bot

    def claim_next(self) -> dict | None:
        """Reserve a reply without overtaking older unfinished replies in the same chat."""
        reply = TelegramOutboxRow
        older = aliased(reply)
        older_pending = select(older.id).where(
            older.bot_id == reply.bot_id,
            older.chat_id == reply.chat_id,
            older.business_id == reply.business_id,
            older.id < reply.id,
            older.status.in_(("ready", "dispatching")),
        ).exists()
        cooldown = aliased(reply)
        rate_limited = select(cooldown.id).where(
            cooldown.bot_id == self.bot.bot_id,
            cooldown.business_id == self.bot.business_id,
            cooldown.last_error.in_(RATE_LIMIT_ERRORS),
            cooldown.ready_at > func.clock_timestamp(),
        ).exists()
        candidate = (
            select(reply.id)
            .where(
                reply.bot_id == self.bot.bot_id,
                reply.business_id == self.bot.business_id,
                reply.status == "ready",
                reply.ready_at <= func.clock_timestamp(),
                ~older_pending,
                ~rate_limited,
            )
            .order_by(reply.ready_at, reply.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        with database_session(self.database_url) as session:
            job = session.execute(
                update(reply).where(reply.id == candidate.scalar_subquery())
                .values(
                    status="dispatching",
                    attempts=reply.attempts + 1,
                    lease_until=func.clock_timestamp() + timedelta(seconds=SEND_LEASE_SECONDS),
                )
                .returning(*reply.__table__.c)
            ).mappings().first()
            if job is None:
                return None
            origin = session.scalar(select(TelegramUpdate.received_at).where(
                TelegramUpdate.bot_id == job["bot_id"],
                TelegramUpdate.update_id == job["update_id"],
            ))
            return {**job, "origin_at": origin or job["created_at"]}

    def finish(
        self, job: dict, status: str, *, error: str | None = None, retry_after: float = 0,
    ) -> bool:
        retrying = status == "ready"
        reply = TelegramOutboxRow
        with database_session(self.database_url) as session:
            saved = session.scalar(update(reply).where(
                reply.id == job["id"], reply.attempts == job["attempts"],
                reply.status == "dispatching", reply.lease_until > func.clock_timestamp(),
            ).values(
                status=status, last_error=error, lease_until=None,
                ready_at=func.clock_timestamp() + timedelta(seconds=retry_after),
                finished_at=None if retrying else func.clock_timestamp(),
                message=reply.message if retrying else None,
                reply_markup=reply.reply_markup if retrying else None,
            ).returning(reply.id))
        return saved is not None

    def recover_expired(self, limit: int = 50) -> int:
        """An expired send may already be visible in Telegram, so avoid automatic replay."""
        reply = TelegramOutboxRow
        with database_session(self.database_url) as session:
            expired = select(reply.id).where(
                reply.bot_id == self.bot.bot_id,
                reply.business_id == self.bot.business_id,
                reply.status == "dispatching",
                reply.lease_until <= func.clock_timestamp(),
            ).order_by(reply.lease_until).limit(limit).with_for_update(skip_locked=True)
            recovered = session.scalars(update(reply).where(
                reply.id.in_(expired), reply.status == "dispatching",
            ).values(
                status="ambiguous", message=None, reply_markup=None, lease_until=None,
                finished_at=func.clock_timestamp(), last_error="dispatch_expired",
            ).returning(reply.id)).all()
        return len(recovered)
