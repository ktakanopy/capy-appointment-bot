"""Coordinate the work required to process one incoming Telegram message.

PostgreSQL keeps the incoming message, the chat's current claim, and replies
waiting to be sent. Redis stores the conversation checkpoint, which is the
saved draft and history the next message can build on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import BigInteger, cast, extract, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.channels.bots import TelegramBot
from app.database import database_session
from app.db_models import (
    TelegramChatProcessingState,
    TelegramOutboxRow,
    TelegramUpdate,
)
from app.observability import debug_event

LEASE_SECONDS = 90
UNKNOWN_OUTCOME = (
    "I couldn't confirm what happened to your previous request. "
    "Please check your appointments before trying it again."
)
REFRESH_OFFER = "That message arrived late. Please ask for available times again."


class ClaimLost(RuntimeError):
    """Stop a write when the worker's lease is no longer valid."""


@dataclass(frozen=True)
class TurnClaim:
    """Record which worker attempt may process a message and write its result.

    A claim includes the bot, the incoming message record, and the generation.
    The generation increases each time a worker claims the chat, including
    retries of the same message.

    `checkpoint_thread` points to the last published conversation state in Redis.
    `interrupted` means an earlier attempt started and never finished.
    `late` means Telegram's message order puts this update before the last
    one the worker recorded for the chat.
    """

    bot: TelegramBot
    row: dict
    generation: int
    checkpoint_thread: str | None
    interrupted: bool
    late: bool

    def active_lease_conditions(self):
        """Build the database checks shared by lease renewals and protected writes.

        Match the bot, chat, message and generation. Use the database clock
        for expiry so every worker checks the same deadline.
        """
        return (
            TelegramChatProcessingState.bot_id == self.bot.bot_id,
            TelegramChatProcessingState.chat_id == self.row["chat_id"],
            TelegramChatProcessingState.business_id == self.bot.business_id,
            TelegramChatProcessingState.active_update_id == self.row["update_id"],
            TelegramChatProcessingState.generation == self.generation,
            TelegramChatProcessingState.lease_until > func.clock_timestamp(),
        )

    def assert_owned(self, session: Session) -> None:
        """Verify that this attempt still owns a live lease.

        Use the caller's transaction to lock the conversation through the write.
        That prevents a replacement worker from taking over between the check
        and a booking or reply update. Raise ClaimLost if the check fails.
        """
        found = session.scalar(
            select(TelegramChatProcessingState.bot_id)
            .where(*self.active_lease_conditions())
            .with_for_update(of=TelegramChatProcessingState, key_share=True)
        )
        if not found:
            raise ClaimLost("Telegram turn lease expired or was superseded.")


@dataclass(frozen=True)
class ChatClaimResult:
    outcome: Literal["claimed", "busy", "not_ready", "empty"]
    claim: TurnClaim | None = None
    retry_after: float | None = None


class TelegramTurns:
    """Give one worker at a time permission to handle messages in a chat.

    A worker claims the next message in a short transaction and receives a
    lease, which is permission to work until a deadline. The heartbeat renews
    that deadline while the model runs, so the worker can release its database
    lock during slow work. If the worker stops, another can take over after
    the lease expires.
    """

    def __init__(self, database_url: str, bot: TelegramBot):
        self.database_url = database_url
        self.bot = bot

    def claim_next(self) -> TurnClaim | None:
        head = self._chat_head_query(TelegramChatProcessingState.chat_id).lateral()
        now = func.clock_timestamp()
        with database_session(self.database_url) as session:
            chat_id = session.scalar(
                select(TelegramChatProcessingState.chat_id)
                .join(head, true())
                .where(
                    TelegramChatProcessingState.bot_id == self.bot.bot_id,
                    TelegramChatProcessingState.business_id == self.bot.business_id,
                    or_(TelegramChatProcessingState.lease_until.is_(None),
                        TelegramChatProcessingState.lease_until <= now),
                    or_(head.c.status == "processing", head.c.ready_at <= now),
                )
                .order_by(head.c.received_at, head.c.update_id)
                .limit(1)
            )
        return self.claim_chat(self.bot.bot_id, chat_id).claim if chat_id is not None else None

    def claim_chat(self, bot_id: int, chat_id: int) -> ChatClaimResult:
        """Reserve the first unfinished message for this chat in PostgreSQL."""
        if bot_id != self.bot.bot_id:
            return ChatClaimResult("empty")
        with database_session(self.database_url) as session:
            chat = session.scalar(
                select(TelegramChatProcessingState)
                .where(
                    TelegramChatProcessingState.bot_id == bot_id,
                    TelegramChatProcessingState.chat_id == chat_id,
                    TelegramChatProcessingState.business_id == self.bot.business_id,
                )
                .with_for_update(of=TelegramChatProcessingState, key_share=True, skip_locked=True)
            )
            if chat is None:
                exists = session.get(TelegramChatProcessingState, (bot_id, chat_id))
                return ChatClaimResult("busy" if exists and exists.business_id == self.bot.business_id else "empty")
            checked_at = session.scalar(select(func.clock_timestamp()))
            if chat.lease_until and chat.lease_until > checked_at:
                return ChatClaimResult("busy")
            head = session.execute(self._chat_head_query(chat_id)).mappings().first()
            if not head:
                return ChatClaimResult("empty")
            if head["status"] != "processing" and head["ready_at"] > checked_at:
                return ChatClaimResult("not_ready", retry_after=(head["ready_at"] - checked_at).total_seconds())
            row = {
                **head,
                "previous_generation": chat.generation,
                "checkpoint_thread": chat.checkpoint_thread,
                "last_message_date": chat.last_message_date,
                "last_update_id": chat.last_update_id,
            }
            claim = self._start_attempt(session, row)
            return ChatClaimResult("claimed", claim) if claim else ChatClaimResult("empty")

    def _chat_head_query(self, chat_id):
        """Find the oldest unfinished message, including work interrupted by a crash."""
        message_time = func.coalesce(
            TelegramUpdate.message_date,
            cast(extract("epoch", TelegramUpdate.received_at), BigInteger),
        )
        return (
            select(*TelegramUpdate.__table__.c)
            .where(
                TelegramUpdate.bot_id == self.bot.bot_id,
                TelegramUpdate.business_id == self.bot.business_id,
                TelegramUpdate.chat_id == chat_id,
                TelegramUpdate.status.in_(("received", "processing")),
            )
            .order_by(
                (TelegramUpdate.status == "processing").desc(),
                message_time,
                TelegramUpdate.update_id,
            )
            .limit(1)
        )

    def _start_attempt(self, session: Session, row: dict) -> TurnClaim | None:
        """Start an attempt and assign a fresh lease in the caller's transaction.

        Mark the message as processing and increment the chat's generation.
        The worker carries that generation to later writes, so an older
        attempt cannot finish the same message after another worker takes over.
        Return None if the update no longer meets the claim conditions.
        """
        # Recheck eligibility because a separate query selected this message.
        receipt = session.scalar(
            update(TelegramUpdate)
            .where(
                TelegramUpdate.bot_id == row["bot_id"],
                TelegramUpdate.update_id == row["update_id"],
                TelegramUpdate.status == row["status"],
                or_(
                    TelegramUpdate.status == "processing",
                    TelegramUpdate.ready_at <= func.clock_timestamp(),
                ),
            )
            .values(status="processing", attempts=TelegramUpdate.attempts + 1)
            .returning(TelegramUpdate.update_id)
        )
        if receipt is None:
            return None
        generation = row["previous_generation"] + 1
        session.execute(
            update(TelegramChatProcessingState)
            .where(
                TelegramChatProcessingState.bot_id == row["bot_id"],
                TelegramChatProcessingState.chat_id == row["chat_id"],
            )
            .values(
                generation=generation,
                active_update_id=row["update_id"],
                lease_until=func.clock_timestamp() + timedelta(seconds=LEASE_SECONDS),
                last_claimed_at=func.clock_timestamp(),
            )
        )
        # A late reply like "2" could refer to an offer the bot has replaced.
        message_date, last_message_date = row["message_date"], row["last_message_date"]
        late = (
            message_date is not None
            and last_message_date is not None
            and (message_date, row["update_id"])
            < (last_message_date, row["last_update_id"])
        )
        return TurnClaim(
            self.bot,
            dict(row),
            generation,
            row["checkpoint_thread"],
            row["status"] == "processing",
            late,
        )

    def renew(self, claim: TurnClaim) -> bool:
        """Give the same worker more time without changing the attempt number.

        The heartbeat calls this during model work. Return False if the claim
        has expired or a newer attempt has taken over, so the heartbeat stops.
        """
        with database_session(self.database_url) as session:
            renewed = session.scalar(
                update(TelegramChatProcessingState)
                .where(
                    *claim.active_lease_conditions(),
                )
                .values(
                    lease_until=func.clock_timestamp()
                    + timedelta(seconds=LEASE_SECONDS),
                )
                .returning(TelegramChatProcessingState.generation)
            )
            return renewed is not None

    def defer_before_execution(self, claim: TurnClaim, failure: str) -> None:
        """Retry a message after a setup failure such as a Redis outage.

        Use this before processing can change customer or booking data. In
        one transaction, put the message back in the queue, release the chat's
        lease, and make the message eligible to retry in one minute.
        """
        with database_session(self.database_url) as session:
            claim.assert_owned(session)
            # A fresh attempt can process a received message normally after setup recovers.
            session.execute(
                update(TelegramUpdate)
                .where(
                    TelegramUpdate.bot_id == claim.bot.bot_id,
                    TelegramUpdate.update_id == claim.row["update_id"],
                )
                .values(
                    status="received",
                    last_error=failure,
                    ready_at=func.clock_timestamp() + timedelta(minutes=1),
                )
            )
            session.execute(
                update(TelegramChatProcessingState)
                .where(
                    TelegramChatProcessingState.bot_id == claim.bot.bot_id,
                    TelegramChatProcessingState.chat_id == claim.row["chat_id"],
                )
                .values(active_update_id=None, lease_until=None)
            )
        debug_event("telegram.turn.retry_scheduled", reason=failure)

    def finish(
        self, claim: TurnClaim, reply: str | None, checkpoint: str | None
    ) -> None:
        """Finish the message and make its reply and saved state available together.

        Commit the completed message, the reference to its Redis checkpoint,
        and any outgoing reply in one transaction.

        A reply is optional. Passing None for the checkpoint clears the
        reference so the next turn can start with a fresh draft.
        """
        row = claim.row
        with database_session(self.database_url) as session:
            claim.assert_owned(session)
            if reply:
                # Keep one outbox row per incoming update even if the work is retried.
                session.execute(
                    insert(TelegramOutboxRow)
                    .values(
                        business_id=row["business_id"],
                        bot_id=row["bot_id"],
                        chat_id=row["chat_id"],
                        update_id=row["update_id"],
                        message=reply,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["bot_id", "update_id"],
                    )
                )
            # Drop the raw payload but keep the update ID to recognize duplicate deliveries.
            session.execute(
                update(TelegramUpdate)
                .where(
                    TelegramUpdate.bot_id == row["bot_id"],
                    TelegramUpdate.update_id == row["update_id"],
                )
                .values(
                    status="processed",
                    payload=None,
                    last_error=None,
                    expires_at=func.clock_timestamp() + timedelta(days=30),
                )
            )
            conversation = session.get(
                TelegramChatProcessingState, (row["bot_id"], row["chat_id"])
            )
            conversation.active_update_id = None
            conversation.lease_until = None
            conversation.checkpoint_thread = checkpoint
            # Keep the latest known message position even if we just handled an older reply.
            if not claim.late and row["message_date"] is not None:
                conversation.last_message_date = row["message_date"]
                conversation.last_update_id = row["update_id"]
        debug_event(
            "telegram.turn.saved",
            has_reply=bool(reply),
            has_checkpoint=checkpoint is not None,
        )
