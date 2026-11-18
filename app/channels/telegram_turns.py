"""Claim and finish a Telegram turn without holding locks during inference."""

from __future__ import annotations

from dataclasses import dataclass

from app.channels.bots import TelegramBot
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
                 AND b.enabled FOR UPDATE OF c""",
            self.key,
        ).fetchone()
        if not found:
            raise ClaimLost("Telegram turn lease expired or was superseded.")


class TelegramTurns:
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
                   LIMIT 1 FOR UPDATE OF c SKIP LOCKED""",
                (lane, lane, per_business),
            ).fetchone()
            if not row:
                return None
            # Serialize the quota check so workers cannot both claim the last slot for a business.
            quota = connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
                (f"capy.turn.quota:{row['business_id']}",),
            ).fetchone()
            if not quota["acquired"]:
                return None
            active = connection.execute(
                """SELECT count(*) AS total FROM telegram_conversations
                   WHERE business_id=%s AND active_update_id IS NOT NULL
                     AND lease_until>clock_timestamp()""",
                (row["business_id"],),
            ).fetchone()
            if active["total"] >= per_business:
                return None
            # The first query could have read this receipt before another worker finished it.
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
        debug_event("telegram.turn.retry_scheduled", reason=failure)

    def finish(self, claim: TurnClaim, reply: str | None, checkpoint: str | None) -> None:
        row = claim.row
        with connect(self.database_url) as connection:
            claim.assert_owned(connection)
            if reply:
                connection.execute(
                    """INSERT INTO telegram_outbox
                           (business_id, bot_id, chat_id, update_id, message)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT (bot_id, update_id) DO NOTHING""",
                    (row["business_id"], row["bot_id"], row["chat_id"], row["update_id"], reply),
                )
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
        debug_event("telegram.turn.saved", has_reply=bool(reply), has_checkpoint=checkpoint is not None)
