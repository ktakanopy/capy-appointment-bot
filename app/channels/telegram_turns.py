"""Claim and finish a Telegram turn without holding locks during inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import BigInteger, cast, extract, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.channels.bots import TelegramBot
from app.channels.telegram_publications import schedule_publication
from app.database import database_session
from app.db_models import TelegramBotRow, TelegramConversation, TelegramOutboxRow, TelegramUpdate
from app.observability import debug_event

LEASE_SECONDS = 90
UNKNOWN_OUTCOME = (
    "I couldn't confirm what happened to your previous request. "
    "Please check your appointments before trying it again."
)
REFRESH_OFFER = "That message arrived late. Please ask for available times again."


FIND_NEXT_TURN = text("""WITH heads AS (
        SELECT DISTINCT ON (u.bot_id,u.chat_id) u.*
        FROM telegram_updates u WHERE u.status IN ('received','processing')
          AND u.chat_id IS NOT NULL
        ORDER BY u.bot_id,u.chat_id,(u.status='processing') DESC,
                 COALESCE(u.message_date, extract(epoch FROM u.received_at)::bigint),
                 u.update_id
    )
    SELECT u.*, b.token_env, c.generation AS previous_generation,
           c.checkpoint_thread, c.last_message_date, c.last_update_id
    FROM heads u JOIN telegram_conversations c ON u.bot_id=c.bot_id
      AND u.chat_id=c.chat_id AND u.business_id=c.business_id
    JOIN telegram_bots b ON b.bot_id=c.bot_id AND b.enabled
    WHERE (c.lease_until IS NULL OR c.lease_until<=clock_timestamp())
      AND (u.status='processing' OR u.ready_at<=clock_timestamp())
      AND (:lane='all' OR u.status='processing'
           OR (:lane='audio')=COALESCE(
               (u.payload->'message') ?| array['voice','audio'], false))
      AND (SELECT count(*) FROM telegram_conversations active
           WHERE active.business_id=c.business_id AND active.active_update_id IS NOT NULL
             AND active.lease_until>clock_timestamp()) < :per_business
    ORDER BY c.last_claimed_at NULLS FIRST, u.received_at, u.bot_id, u.update_id
    LIMIT 1 FOR NO KEY UPDATE OF c SKIP LOCKED""")


class ClaimLost(RuntimeError):
    """The worker may no longer publish state or change a booking."""


@dataclass(frozen=True)
class TurnClaim:
    bot: TelegramBot
    row: dict
    generation: int
    checkpoint_thread: str | None
    interrupted: bool
    late: bool

    def active_lease_conditions(self):
        """Use the same ownership check for heartbeat renewals and protected writes."""
        return (
            TelegramConversation.bot_id == self.bot.bot_id,
            TelegramConversation.chat_id == self.row["chat_id"],
            TelegramConversation.active_update_id == self.row["update_id"],
            TelegramConversation.generation == self.generation,
            TelegramConversation.lease_until > func.clock_timestamp(),
        )

    def assert_owned(self, session: Session) -> None:
        """Lock the conversation until commit so a replacement worker cannot take over mid-write."""
        found = session.scalar(
            select(TelegramConversation.bot_id)
            .join(TelegramBotRow, TelegramBotRow.bot_id == TelegramConversation.bot_id)
            .where(*self.active_lease_conditions(), TelegramBotRow.enabled)
            .with_for_update(of=TelegramConversation, key_share=True)
        )
        if not found:
            raise ClaimLost("Telegram turn lease expired or was superseded.")


class TelegramTurns:
    """Coordinate processing of incoming Telegram messages in PostgreSQL.

    A renewable lease lets one worker handle a chat at a time without holding
    a database lock during inference. Finishing a turn saves its reply and
    checkpoint reference together so the next worker sees completed state.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url

    def claim(self, *, lane: str = "all", per_business: int = 4) -> TurnClaim | None:
        with database_session(self.database_url) as session:
            row = session.execute(
                FIND_NEXT_TURN, {"lane": lane, "per_business": per_business},
            ).mappings().first()
            if not row:
                return None
            if not self._has_capacity(session, row["business_id"], per_business):
                return None
            return self._start_attempt(session, row)

    def claim_chat(self, bot_id: int, chat_id: int, *, per_business: int = 4) -> TurnClaim | None:
        """Select one chat head or persist a wake-up when it cannot run yet."""
        with database_session(self.database_url) as session:
            chat_details = session.execute(
                select(TelegramConversation, TelegramBotRow.enabled, TelegramBotRow.token_env,
                       func.clock_timestamp())
                .join(TelegramBotRow, (TelegramBotRow.bot_id == TelegramConversation.bot_id)
                      & (TelegramBotRow.business_id == TelegramConversation.business_id))
                .where(TelegramConversation.bot_id == bot_id, TelegramConversation.chat_id == chat_id)
                .with_for_update(of=TelegramConversation, key_share=True)
            ).first()
            if chat_details is None:
                return None
            head = self._chat_head(session, bot_id, chat_id)
            if not head:
                return None
            chat, enabled, token_env, checked_at = chat_details
            ready_at = head["ready_at"] if head["status"] == "received" else checked_at
            ready_at = max(ready_at, chat.lease_until or checked_at)
            if not enabled:
                schedule_publication(session, bot_id=bot_id, chat_id=chat_id, delay=60)
                return None
            if ready_at > checked_at:
                schedule_publication(session, bot_id=bot_id, chat_id=chat_id, due_at=ready_at)
                return None
            if not self._has_capacity(session, chat.business_id, per_business):
                schedule_publication(session, bot_id=bot_id, chat_id=chat_id, delay=1)
                return None
            row = {**head, "token_env": token_env, "previous_generation": chat.generation,
                   "checkpoint_thread": chat.checkpoint_thread, "last_message_date": chat.last_message_date,
                   "last_update_id": chat.last_update_id}
            return self._start_attempt(session, row)

    @staticmethod
    def _chat_head(session: Session, bot_id: int, chat_id: int) -> dict | None:
        """Keep interrupted work and older Telegram messages ahead of later turns."""
        message_time = func.coalesce(
            TelegramUpdate.message_date, cast(extract("epoch", TelegramUpdate.received_at), BigInteger),
        )
        statement = select(*TelegramUpdate.__table__.c).where(
            TelegramUpdate.bot_id == bot_id, TelegramUpdate.chat_id == chat_id,
            TelegramUpdate.status.in_(("received", "processing")),
        ).order_by(
            (TelegramUpdate.status == "processing").desc(), message_time, TelegramUpdate.update_id,
        ).limit(1)
        return session.execute(statement).mappings().first()

    @staticmethod
    def _has_capacity(session: Session, business_id: str, limit: int) -> bool:
        """Serialize reservations so two chats cannot claim a business's last slot."""
        lock_key = func.hashtextextended(f"capy.turn.quota:{business_id}", 0)
        if not session.scalar(select(func.pg_try_advisory_xact_lock(lock_key))):
            return False
        active = session.scalar(select(func.count()).select_from(TelegramConversation).where(
            TelegramConversation.business_id == business_id,
            TelegramConversation.active_update_id.is_not(None),
            TelegramConversation.lease_until > func.clock_timestamp(),
        ))
        return active < limit

    @staticmethod
    def _start_attempt(session: Session, row: dict) -> TurnClaim | None:
        """Record a generation that later booking and reply writes must prove they own."""
        receipt = session.scalar(update(TelegramUpdate).where(
            TelegramUpdate.bot_id == row["bot_id"], TelegramUpdate.update_id == row["update_id"],
            TelegramUpdate.status == row["status"],
            or_(TelegramUpdate.status == "processing", TelegramUpdate.ready_at <= func.clock_timestamp()),
        ).values(status="processing", attempts=TelegramUpdate.attempts + 1).returning(TelegramUpdate.update_id))
        if receipt is None:
            return None
        generation = row["previous_generation"] + 1
        session.execute(update(TelegramConversation).where(
            TelegramConversation.bot_id == row["bot_id"], TelegramConversation.chat_id == row["chat_id"],
        ).values(
            generation=generation, active_update_id=row["update_id"],
            lease_until=func.clock_timestamp() + timedelta(seconds=LEASE_SECONDS),
            last_claimed_at=func.clock_timestamp(),
        ))
        message_date, last_message_date = row["message_date"], row["last_message_date"]
        late = message_date is not None and last_message_date is not None and (
            message_date, row["update_id"]
        ) < (last_message_date, row["last_update_id"])
        bot = TelegramBot(row["bot_id"], row["business_id"], row["token_env"])
        return TurnClaim(bot, dict(row), generation, row["checkpoint_thread"],
                         row["status"] == "processing", late)

    def renew(self, claim: TurnClaim) -> bool:
        """Extend an owned lease so long inference can finish safely."""
        with database_session(self.database_url) as session:
            renewed = session.scalar(update(TelegramConversation).where(
                *claim.active_lease_conditions(),
            ).values(
                lease_until=func.clock_timestamp() + timedelta(seconds=LEASE_SECONDS),
            ).returning(TelegramConversation.generation))
            return renewed is not None

    def defer_before_execution(self, claim: TurnClaim, failure: str) -> None:
        """Reschedule setup failures that occurred before any write or inference."""
        with database_session(self.database_url) as session:
            claim.assert_owned(session)
            session.execute(update(TelegramUpdate).where(
                TelegramUpdate.bot_id == claim.bot.bot_id, TelegramUpdate.update_id == claim.row["update_id"],
            ).values(
                status="received", last_error=failure,
                ready_at=func.clock_timestamp() + timedelta(minutes=1),
            ))
            session.execute(update(TelegramConversation).where(
                TelegramConversation.bot_id == claim.bot.bot_id,
                TelegramConversation.chat_id == claim.row["chat_id"],
            ).values(active_update_id=None, lease_until=None))
            schedule_publication(session, bot_id=claim.bot.bot_id, chat_id=claim.row["chat_id"], delay=60)
        debug_event("telegram.turn.retry_scheduled", reason=failure)

    def finish(self, claim: TurnClaim, reply: str | None, checkpoint: str | None) -> None:
        """Commit reply, checkpoint pointer and wake-ups before acknowledging the turn."""
        row = claim.row
        with database_session(self.database_url) as session:
            claim.assert_owned(session)
            if reply:
                outgoing_id = session.scalar(insert(TelegramOutboxRow).values(
                    business_id=row["business_id"], bot_id=row["bot_id"],
                    chat_id=row["chat_id"], update_id=row["update_id"], message=reply,
                ).on_conflict_do_nothing(
                    index_elements=["bot_id", "update_id"],
                ).returning(TelegramOutboxRow.id))
                if outgoing_id is not None:
                    schedule_publication(session, outgoing_id=outgoing_id)
            session.execute(update(TelegramUpdate).where(
                TelegramUpdate.bot_id == row["bot_id"], TelegramUpdate.update_id == row["update_id"],
            ).values(
                status="delivered", payload=None, last_error=None,
                expires_at=func.clock_timestamp() + timedelta(days=30),
            ))
            conversation = session.get(TelegramConversation, (row["bot_id"], row["chat_id"]))
            conversation.active_update_id = None
            conversation.lease_until = None
            conversation.checkpoint_thread = checkpoint
            if not claim.late and row["message_date"] is not None:
                conversation.last_message_date = row["message_date"]
                conversation.last_update_id = row["update_id"]
            next_turn = self._chat_head(session, row["bot_id"], row["chat_id"])
            if next_turn:
                schedule_publication(session, bot_id=row["bot_id"], chat_id=row["chat_id"],
                                     due_at=next_turn["ready_at"])
        debug_event("telegram.turn.saved", has_reply=bool(reply), has_checkpoint=checkpoint is not None)
