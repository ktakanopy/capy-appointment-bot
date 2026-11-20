"""Dispatch durable replies with Telegram rate budgets."""

from __future__ import annotations

import random
import time
from datetime import timedelta

from psycopg.errors import LockNotAvailable
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, aliased

from app.channels.bots import TelegramBot
from app.channels.telegram import TelegramApiError
from app.channels.telegram_publications import schedule_publication
from app.database import database_session
from app.db_models import (
    TelegramBotDelivery, TelegramBotRow, TelegramChatDelivery, TelegramOutboxRow,
    TelegramUpdate,
)
from app.observability import debug_event, log_event

MAX_ATTEMPTS = 5
SEND_LEASE_SECONDS = 45
BOT_SEND_INTERVAL = 0.05


def _outgoing_details():
    return select(
        *TelegramOutboxRow.__table__.c,
        TelegramBotRow.token_env,
        func.coalesce(TelegramUpdate.received_at, TelegramOutboxRow.created_at)
        .label("origin_at"),
    ).join(
        TelegramBotRow, and_(
            TelegramBotRow.bot_id == TelegramOutboxRow.bot_id,
            TelegramBotRow.business_id == TelegramOutboxRow.business_id,
            TelegramBotRow.enabled,
        ),
    ).outerjoin(
        TelegramUpdate, and_(
            TelegramUpdate.bot_id == TelegramOutboxRow.bot_id,
            TelegramUpdate.update_id == TelegramOutboxRow.update_id,
        ),
    )


def _older_pending_send():
    older = aliased(TelegramOutboxRow)
    return select(older.id).where(
        older.bot_id == TelegramOutboxRow.bot_id,
        older.chat_id == TelegramOutboxRow.chat_id,
        older.id < TelegramOutboxRow.id,
        older.status.in_(("ready", "dispatching")),
    ).exists()


def _with_rate_budgets(statement):
    return statement.outerjoin(
        TelegramBotDelivery, TelegramBotDelivery.bot_id == TelegramOutboxRow.bot_id,
    ).outerjoin(
        TelegramChatDelivery, and_(
            TelegramChatDelivery.bot_id == TelegramOutboxRow.bot_id,
            TelegramChatDelivery.chat_id == TelegramOutboxRow.chat_id,
        ),
    )


class TelegramDelivery:
    def send(self, job: dict) -> None:
        bot = TelegramBot(job["bot_id"], job["business_id"], job["token_env"])
        bot.client().send_message(job["chat_id"], job["message"], job["reply_markup"])


class TelegramOutbox:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def claim(self) -> dict | None:
        """Authorize a ready dispatch and reserve both rate budgets atomically."""
        statement = _with_rate_budgets(_outgoing_details()).where(
            TelegramOutboxRow.status == "ready",
            TelegramOutboxRow.ready_at <= func.clock_timestamp(),
            or_(TelegramBotDelivery.available_at.is_(None),
                TelegramBotDelivery.available_at <= func.clock_timestamp()),
            or_(TelegramChatDelivery.available_at.is_(None),
                TelegramChatDelivery.available_at <= func.clock_timestamp()),
            ~_older_pending_send(),
        ).order_by(TelegramOutboxRow.ready_at, TelegramOutboxRow.id).limit(16)
        with database_session(self.database_url) as session:
            candidates = session.execute(statement).mappings().all()
        for job in candidates:
            claimed = self._try_authorize(job)
            if claimed:
                return claimed
        return None

    def claim_by_id(self, outgoing_id: int) -> dict | None:
        """Reserve the broker-selected send or persist a wake-up if delivery is blocked."""
        statement = _outgoing_details().where(
            TelegramOutboxRow.id == outgoing_id, TelegramOutboxRow.status == "ready",
            TelegramOutboxRow.ready_at <= func.clock_timestamp(),
        )
        with database_session(self.database_url) as session:
            job = session.execute(statement).mappings().first()
        if job:
            claimed = self._try_authorize(job)
            if claimed:
                return claimed
        self._wake_when_ready(outgoing_id)
        return None

    def _try_authorize(self, job) -> dict | None:
        """Skip contended rows so other chats can still receive messages."""
        try:
            return self._authorize(job)
        except DBAPIError as exc:
            if not isinstance(exc.orig, LockNotAvailable):
                raise
            return None

    def _wake_when_ready(self, outgoing_id: int) -> None:
        """Keep blocked sends pending without holding a broker delivery or retrying the API."""
        delay = case(
            (~TelegramBotRow.enabled, timedelta(minutes=1)),
            (_older_pending_send(), timedelta(seconds=0.25)),
            else_=timedelta(seconds=BOT_SEND_INTERVAL),
        )
        due = select(func.greatest(
            TelegramOutboxRow.ready_at,
            TelegramBotDelivery.available_at,
            TelegramChatDelivery.available_at,
            func.clock_timestamp() + delay,
        )).select_from(TelegramOutboxRow).join(
            TelegramBotRow, and_(
                TelegramBotRow.bot_id == TelegramOutboxRow.bot_id,
                TelegramBotRow.business_id == TelegramOutboxRow.business_id,
            ),
        )
        with database_session(self.database_url) as session:
            due_at = session.scalar(_with_rate_budgets(due).where(
                TelegramOutboxRow.id == outgoing_id, TelegramOutboxRow.status == "ready",
            ))
            if due_at is not None:
                schedule_publication(session, outgoing_id=outgoing_id, due_at=due_at)

    def _authorize(self, job) -> dict | None:
        """Check current permissions, chat ordering and rate budgets before the HTTP call."""
        with database_session(self.database_url) as session:
            token_env = session.scalar(select(TelegramBotRow.token_env).where(
                TelegramBotRow.enabled, TelegramBotRow.bot_id == job["bot_id"],
                TelegramBotRow.business_id == job["business_id"],
            ).with_for_update(read=True, nowait=True))
            if token_env is None:
                return None
            locked = session.scalar(select(TelegramOutboxRow.id).where(
                TelegramOutboxRow.id == job["id"], TelegramOutboxRow.status == "ready",
                TelegramOutboxRow.ready_at <= func.clock_timestamp(), ~_older_pending_send(),
            ).with_for_update(skip_locked=True))
            if locked is None or not self._reserve(session, job):
                return None
            attempts, lease_until = session.execute(update(TelegramOutboxRow).where(
                TelegramOutboxRow.id == job["id"],
            ).values(
                status="dispatching", attempts=TelegramOutboxRow.attempts + 1,
                lease_until=func.clock_timestamp() + timedelta(seconds=SEND_LEASE_SECONDS),
            ).returning(TelegramOutboxRow.attempts, TelegramOutboxRow.lease_until)).one()
            return {**job, "token_env": token_env, "attempts": attempts, "lease_until": lease_until}

    @staticmethod
    def _reserve(session: Session, job) -> bool:
        bot_id, chat_id = job["bot_id"], job["chat_id"]
        session.execute(insert(TelegramBotDelivery).values(bot_id=bot_id).on_conflict_do_nothing())
        eligible = session.scalar(select(
            TelegramBotDelivery.available_at <= func.clock_timestamp(),
        ).where(TelegramBotDelivery.bot_id == bot_id).with_for_update(nowait=True))
        if not eligible:
            return False
        budget = insert(TelegramChatDelivery).values(
            bot_id=bot_id, chat_id=chat_id, available_at=func.clock_timestamp() + timedelta(seconds=1),
        )
        reserved = session.scalar(budget.on_conflict_do_update(
            index_elements=["bot_id", "chat_id"], set_={"available_at": budget.excluded.available_at},
            where=TelegramChatDelivery.available_at <= func.clock_timestamp(),
        ).returning(TelegramChatDelivery.chat_id))
        if reserved is None:
            return False
        session.execute(update(TelegramBotDelivery).where(TelegramBotDelivery.bot_id == bot_id).values(
            available_at=func.clock_timestamp() + timedelta(seconds=BOT_SEND_INTERVAL),
        ))
        return True

    def finish(
        self, job: dict, status: str, *, error: str | None = None, retry_after: float = 0,
        bot_cooldown: int | None = None,
    ) -> bool:
        """Record the provider outcome and schedule any safe retry before acknowledgement."""
        retrying = status == "ready"
        with database_session(self.database_url) as session:
            ready_at = session.scalar(update(TelegramOutboxRow).where(
                TelegramOutboxRow.id == job["id"], TelegramOutboxRow.attempts == job["attempts"],
                TelegramOutboxRow.status == "dispatching",
                TelegramOutboxRow.lease_until > func.clock_timestamp(),
            ).values(
                status=status, last_error=error, lease_until=None,
                ready_at=func.clock_timestamp() + timedelta(seconds=retry_after),
                finished_at=None if retrying else func.clock_timestamp(),
                message=TelegramOutboxRow.message if retrying else None,
                reply_markup=TelegramOutboxRow.reply_markup if retrying else None,
            ).returning(TelegramOutboxRow.ready_at))
            if ready_at is None:
                return False
            if retrying:
                schedule_publication(session, outgoing_id=job["id"], due_at=ready_at)
            if bot_cooldown:
                session.execute(update(TelegramBotDelivery).where(
                    TelegramBotDelivery.bot_id == job["bot_id"],
                ).values(available_at=func.greatest(
                    TelegramBotDelivery.available_at, func.clock_timestamp() + timedelta(seconds=bot_cooldown),
                )))
        return True

    def recover_expired(self, limit: int = 50) -> int:
        """Expired sends are ambiguous. Automatic replay could message a user twice."""
        expired = (
            TelegramOutboxRow.status == "dispatching",
            TelegramOutboxRow.lease_until <= func.clock_timestamp(),
        )
        with database_session(self.database_url) as session:
            jobs = session.execute(select(
                TelegramOutboxRow.id,
            ).where(*expired).order_by(TelegramOutboxRow.lease_until).limit(limit)).mappings().all()
            recovered = 0
            for job in jobs:
                updated = session.scalar(update(TelegramOutboxRow).where(
                    TelegramOutboxRow.id == job["id"], *expired,
                ).values(
                    status="ambiguous", message=None, reply_markup=None, lease_until=None,
                    finished_at=func.clock_timestamp(), last_error="dispatch_expired",
                ).returning(TelegramOutboxRow.id))
                if updated is not None:
                    recovered += 1
        return recovered


def send_next(outbox: TelegramOutbox, delivery: TelegramDelivery, logger) -> bool:
    """Drain one send using the PostgreSQL rollback worker."""
    job = outbox.claim()
    return deliver_claimed(outbox, delivery, logger, job)


def deliver_claimed(outbox: TelegramOutbox, delivery: TelegramDelivery, logger, job: dict | None) -> bool:
    """Call Telegram once for a reserved send and persist its classified outcome."""
    if job is None:
        return False
    started = time.monotonic()
    kind = "reply"
    debug_event("telegram.outbox.claimed", bot_id=job["bot_id"], update_id=job.get("update_id"),
                kind=kind, attempt=job["attempts"])
    try:
        delivery.send(job)
    except TelegramApiError as error:
        cooldown = error.retry_after if error.code in ("telegram_api_429", "telegram_http_429") else None
        if error.code == "telegram_transport_error":
            outcome = "ambiguous"
        elif error.is_permanent_rejection or job["attempts"] >= MAX_ATTEMPTS:
            outcome = "failed"
        else:
            outcome = "ready"
        delay = error.retry_after or min(2 ** job["attempts"], 60) + random.random()
        recorded = outbox.finish(job, outcome, error=error.code, retry_after=delay, bot_cooldown=cooldown)
    except RuntimeError:
        outcome = "failed"
        recorded = outbox.finish(job, outcome, error="delivery_configuration")
    else:
        outcome = "sent"
        recorded = outbox.finish(job, outcome)
    debug_event("telegram.outbox.result", bot_id=job["bot_id"], update_id=job.get("update_id"),
                kind=kind, outcome=outcome if recorded else "claim_lost")
    log_event(logger, "telegram.outbox.finished", bot_id=job["bot_id"],
              outcome=outcome if recorded else "claim_lost", provider_outcome=outcome,
              kind=kind,
              elapsed_since_origin_ms=round((time.time() - job["origin_at"].timestamp()) * 1000),
              duration_ms=round((time.monotonic() - started) * 1000))
    return True
