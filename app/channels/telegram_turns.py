"""Claim and finish a Telegram turn without holding locks during inference."""

from __future__ import annotations

from dataclasses import dataclass

from app.channels.bots import TelegramBot
from app.channels.telegram_publications import schedule_publication
from app.database import connect
from app.observability import debug_event

LEASE_SECONDS = 90
UNKNOWN_OUTCOME = (
    "I couldn't confirm what happened to your previous request. "
    "Please check your appointments before trying it again."
)
REFRESH_OFFER = "That message arrived late. Please ask for available times again."


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

    @property
    def key(self) -> tuple[int, int, int, int]:
        return (self.bot.bot_id, self.row["chat_id"], self.row["update_id"], self.generation)

    def assert_owned(self, connection) -> None:
        """Lock the conversation until commit so a replacement worker cannot take over mid-write."""
        found = connection.execute(
            """SELECT 1 FROM telegram_conversations c
               JOIN telegram_bots b ON b.bot_id=c.bot_id
               WHERE c.bot_id=%s AND c.chat_id=%s AND c.active_update_id=%s
                 AND c.generation=%s AND c.lease_until>clock_timestamp()
                 AND b.enabled FOR NO KEY UPDATE OF c""",
            self.key,
        ).fetchone()
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
        with connect(self.database_url) as connection:
            row = connection.execute(
                """WITH heads AS (
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
                     AND (%s='all' OR u.status='processing'
                          OR (%s='audio')=COALESCE(
                              (u.payload->'message') ?| array['voice','audio'], false))
                     AND (SELECT count(*) FROM telegram_conversations active
                          WHERE active.business_id=c.business_id AND active.active_update_id IS NOT NULL
                            AND active.lease_until>clock_timestamp()) < %s
                   ORDER BY c.last_claimed_at NULLS FIRST, u.received_at, u.bot_id, u.update_id
                   LIMIT 1 FOR NO KEY UPDATE OF c SKIP LOCKED""",
                (lane, lane, per_business),
            ).fetchone()
            if not row:
                return None
            if not self._has_capacity(connection, row["business_id"], per_business):
                return None
            return self._start_attempt(connection, row)

    def claim_chat(self, bot_id: int, chat_id: int, *, per_business: int = 4) -> TurnClaim | None:
        """Select one chat head or persist a wake-up when it cannot run yet."""
        with connect(self.database_url) as connection:
            chat = connection.execute(
                """SELECT c.*, b.enabled, b.token_env, clock_timestamp() AS checked_at
                   FROM telegram_conversations c JOIN telegram_bots b USING (bot_id,business_id)
                   WHERE c.bot_id=%s AND c.chat_id=%s FOR NO KEY UPDATE OF c""",
                (bot_id, chat_id),
            ).fetchone()
            if not chat:
                return None
            head = self._chat_head(connection, bot_id, chat_id)
            if not head:
                return None
            checked_at = chat["checked_at"]
            ready_at = head["ready_at"] if head["status"] == "received" else checked_at
            ready_at = max(ready_at, chat["lease_until"] or checked_at)
            if not chat["enabled"]:
                schedule_publication(connection, bot_id=bot_id, chat_id=chat_id, delay=60)
                return None
            if ready_at > checked_at:
                schedule_publication(connection, bot_id=bot_id, chat_id=chat_id, due_at=ready_at)
                return None
            if not self._has_capacity(connection, chat["business_id"], per_business):
                schedule_publication(connection, bot_id=bot_id, chat_id=chat_id, delay=1)
                return None
            row = {**head, "token_env": chat["token_env"], "previous_generation": chat["generation"],
                   "checkpoint_thread": chat["checkpoint_thread"], "last_message_date": chat["last_message_date"],
                   "last_update_id": chat["last_update_id"]}
            return self._start_attempt(connection, row)

    @staticmethod
    def _chat_head(connection, bot_id: int, chat_id: int) -> dict | None:
        """Keep interrupted work and older Telegram messages ahead of later turns."""
        return connection.execute(
            """SELECT * FROM telegram_updates WHERE bot_id=%s AND chat_id=%s
               AND status IN ('received','processing')
               ORDER BY (status='processing') DESC,
                        COALESCE(message_date,extract(epoch FROM received_at)::bigint),update_id
               LIMIT 1""",
            (bot_id, chat_id),
        ).fetchone()

    @staticmethod
    def _has_capacity(connection, business_id: str, limit: int) -> bool:
        """Serialize reservations so two chats cannot claim a business's last slot."""
        quota = connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
            (f"capy.turn.quota:{business_id}",),
        ).fetchone()
        if not quota["acquired"]:
            return False
        active = connection.execute(
            """SELECT count(*) AS total FROM telegram_conversations
               WHERE business_id=%s AND active_update_id IS NOT NULL
                 AND lease_until>clock_timestamp()""",
            (business_id,),
        ).fetchone()
        return active["total"] < limit

    @staticmethod
    def _start_attempt(connection, row: dict) -> TurnClaim | None:
        """Record a generation that later booking and reply writes must prove they own."""
        receipt = connection.execute(
            """UPDATE telegram_updates SET status='processing', attempts=attempts+1
               WHERE bot_id=%s AND update_id=%s AND status=%s
                 AND (status='processing' OR ready_at<=clock_timestamp())
               RETURNING update_id""",
            (row["bot_id"], row["update_id"], row["status"]),
        ).fetchone()
        if not receipt:
            return None
        generation = row["previous_generation"] + 1
        connection.execute(
            """UPDATE telegram_conversations SET generation=%s, active_update_id=%s,
                   lease_until=clock_timestamp() + %s * interval '1 second',
                   last_claimed_at=clock_timestamp()
               WHERE bot_id=%s AND chat_id=%s""",
            (generation, row["update_id"], LEASE_SECONDS, row["bot_id"], row["chat_id"]),
        )
        message_date, last_message_date = row["message_date"], row["last_message_date"]
        late = message_date is not None and last_message_date is not None and (
            message_date, row["update_id"]
        ) < (last_message_date, row["last_update_id"])
        bot = TelegramBot(row["bot_id"], row["business_id"], row["token_env"])
        return TurnClaim(bot, row, generation, row["checkpoint_thread"],
                         row["status"] == "processing", late)

    def renew(self, claim: TurnClaim) -> bool:
        """Extend an owned lease so long inference can finish safely."""
        with connect(self.database_url) as connection:
            updated = connection.execute(
                """UPDATE telegram_conversations
                   SET lease_until=clock_timestamp() + %s * interval '1 second'
                   WHERE bot_id=%s AND chat_id=%s AND active_update_id=%s
                     AND generation=%s AND lease_until>clock_timestamp()""",
                (LEASE_SECONDS, *claim.key),
            )
            return updated.rowcount == 1

    def defer_before_execution(self, claim: TurnClaim, failure: str) -> None:
        """Reschedule setup failures that occurred before any write or inference."""
        with connect(self.database_url) as connection:
            claim.assert_owned(connection)
            connection.execute(
                """UPDATE telegram_updates SET status='received', last_error=%s,
                       ready_at=clock_timestamp()+interval '1 minute'
                   WHERE bot_id=%s AND update_id=%s""",
                (failure, claim.bot.bot_id, claim.row["update_id"]),
            )
            connection.execute(
                """UPDATE telegram_conversations SET active_update_id=NULL, lease_until=NULL
                   WHERE bot_id=%s AND chat_id=%s""",
                (claim.bot.bot_id, claim.row["chat_id"]),
            )
            schedule_publication(connection, bot_id=claim.bot.bot_id, chat_id=claim.row["chat_id"], delay=60)
        debug_event("telegram.turn.retry_scheduled", reason=failure)

    def finish(self, claim: TurnClaim, reply: str | None, checkpoint: str | None) -> None:
        """Commit reply, checkpoint pointer and wake-ups before acknowledging the turn."""
        row = claim.row
        with connect(self.database_url) as connection:
            claim.assert_owned(connection)
            if reply:
                outgoing = connection.execute(
                    """INSERT INTO telegram_outbox
                           (business_id, bot_id, chat_id, update_id, message)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT (bot_id, update_id) DO NOTHING RETURNING id""",
                    (row["business_id"], row["bot_id"], row["chat_id"], row["update_id"], reply),
                ).fetchone()
                if outgoing:
                    schedule_publication(connection, outgoing_id=outgoing["id"])
            connection.execute(
                """UPDATE telegram_updates SET status='delivered', payload=NULL,last_error=NULL,
                       expires_at=clock_timestamp()+interval '30 days'
                   WHERE bot_id=%s AND update_id=%s""",
                (row["bot_id"], row["update_id"]),
            )
            advance_message_order = not claim.late and row["message_date"] is not None
            connection.execute(
                """UPDATE telegram_conversations SET active_update_id=NULL, lease_until=NULL,
                       checkpoint_thread=%s,
                       last_message_date=CASE WHEN %s THEN %s ELSE last_message_date END,
                       last_update_id=CASE WHEN %s THEN %s ELSE last_update_id END
                   WHERE bot_id=%s AND chat_id=%s""",
                (checkpoint, advance_message_order, row["message_date"],
                 advance_message_order, row["update_id"],
                 row["bot_id"], row["chat_id"]),
            )
            next_turn = self._chat_head(connection, row["bot_id"], row["chat_id"])
            if next_turn:
                schedule_publication(connection, bot_id=row["bot_id"], chat_id=row["chat_id"],
                                     due_at=next_turn["ready_at"])
        debug_event("telegram.turn.saved", has_reply=bool(reply), has_checkpoint=checkpoint is not None)
