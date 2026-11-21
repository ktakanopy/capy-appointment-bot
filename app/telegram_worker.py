"""Process incoming Telegram messages and save replies for delivery."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from threading import Event, Thread
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError
from sqlalchemy import text

from app.booking import BookingService
from app.channels.bots import TelegramBot, configured_bot
from app.channels.phone import canonical_phone
from app.channels.telegram import TelegramBotApi
from app.channels.telegram_delivery import TelegramOutbox
from app.channels.telegram_intake import private_message
from app.channels.telegram_repository import TelegramRepository
from app.channels.telegram_turns import (
    LEASE_SECONDS,
    REFRESH_OFFER,
    UNKNOWN_OUTCOME,
    ClaimLost,
    TelegramTurns,
    TurnClaim,
)
from app.config import Settings
from app.customer_scheduling_actions import CustomerSchedulingActions
from app.database import DATABASE_ERRORS, database_session
from app.graph.contracts import TurnContext
from app.llm.gemma import ProviderError
from app.models import DomainError
from app.observability import debug_event, debug_turn, log_event, timed_event
from app.runtime import RuntimeContext, close_runtime, create_runtime
from app.telegram_sender import deliver_claimed


def run_worker(settings: Settings, stop: Event, logger) -> None:
    bot = configured_bot(settings)
    if bot is None:
        raise RuntimeError("Set TELEGRAM_BOT_ID to start the worker.")
    bot.client()
    database_url = settings.database_url
    repository = TelegramRepository(database_url)
    lock = repository.acquire_worker_lock(bot.bot_id)
    if lock is None:
        raise RuntimeError("A worker is already running for this bot.")
    with lock:
        _check_bot_scope(database_url, bot)
        runtime = create_runtime(settings)
        try:
            _run_pending(database_url, bot, runtime, stop, logger, lock)
        finally:
            close_runtime(runtime)


def _run_pending(database_url: str, bot: TelegramBot, runtime: RuntimeContext, stop: Event, logger, lock) -> None:
    turns, outbox = TelegramTurns(database_url, bot), TelegramOutbox(database_url, bot)
    next_send = next_recovery = next_prune = 0.0
    while not stop.is_set():
        # Exit and reacquire the worker lock if its PostgreSQL connection dies.
        lock.execute("SELECT 1")
        now = time.monotonic()
        try:
            if now >= next_recovery:
                outbox.recover_expired()
                next_recovery = now + 5
            if now >= next_prune:
                prune_history(database_url)
                next_prune = now + 60

            reply = outbox.claim_next() if now >= next_send else None
            if reply is not None:
                # A single send per second also covers the limit for any individual chat.
                try:
                    deliver_claimed(outbox, logger, reply)
                finally:
                    next_send = time.monotonic() + 1
            if stop.is_set():
                break
            claim = turns.claim_next()
            if claim is not None:
                process_turn(turns, claim, runtime)
            if claim is None and reply is None:
                stop.wait(0.25)
        except ClaimLost:
            log_event(logger, "telegram.turn.claim_lost")
        except DATABASE_ERRORS as error:
            log_event(logger, "telegram.worker.database_retry", failure_type=type(error).__name__)
            stop.wait(1)


def _check_bot_scope(database_url: str, bot: TelegramBot) -> None:
    with database_session(database_url) as session:
        wrong_scope = session.scalar(text("""
            SELECT EXISTS (
                SELECT 1 FROM customers
                WHERE telegram_bot_id = :bot_id AND business_id <> :business_id
                UNION ALL
                SELECT 1 FROM telegram_inbox
                WHERE (bot_id, business_id) <> (:bot_id, :business_id)
                  AND (status IN ('received', 'processing') OR bot_id = :bot_id)
                UNION ALL
                SELECT 1 FROM telegram_conversations
                WHERE bot_id = :bot_id AND business_id <> :business_id
                UNION ALL
                SELECT 1 FROM telegram_outbox
                WHERE (bot_id, business_id) <> (:bot_id, :business_id)
                  AND (status IN ('ready', 'dispatching') OR bot_id = :bot_id)
            )
        """), {"bot_id": bot.bot_id, "business_id": bot.business_id})
    if wrong_scope:
        raise RuntimeError("Pending or linked Telegram data does not match the configured bot and business.")


def process_turn(turns: TelegramTurns, claim: TurnClaim, runtime) -> None:
    with debug_turn(claim.bot.bot_id, claim.row["update_id"]):
        debug_event("telegram.turn.claimed", attempt=claim.generation)
        started = time.monotonic()
        message = private_message(claim.row)
        if message is None:
            debug_event("telegram.turn.skipped", reason="unsupported_message")
            turns.finish(claim, None, claim.checkpoint_thread)
            return
        if claim.interrupted:
            debug_event("telegram.turn.skipped", reason="interrupted_attempt")
            turns.finish(claim, UNKNOWN_OUTCOME, None)
            return
        if claim.late:
            debug_event("telegram.turn.skipped", reason="out_of_order")
            turns.finish(claim, REFRESH_OFFER, claim.checkpoint_thread)
            return

        with _keep_turn_claim_alive(turns, claim):
            worker = _prepare_turn_worker(turns, claim, runtime)
            if worker is None or not _restore_turn_checkpoint(turns, claim, worker):
                return
            reply, checkpoint = worker.handle_message(
                claim.row, message, claim.checkpoint_thread
            )
            turns.finish(claim, reply, checkpoint)
            log_event(
                runtime.logger, "telegram.turn.finished", bot_id=claim.bot.bot_id,
                queue_ms=round((time.time() - claim.row["received_at"].timestamp()) * 1000),
                processing_ms=round((time.monotonic() - started) * 1000),
            )


def _prepare_turn_worker(
    turns: TelegramTurns, claim: TurnClaim, runtime: RuntimeContext
) -> TelegramWorker | None:
    """Set up the worker that will handle one claimed Telegram message.

    A claim gives this attempt temporary ownership of the message. Reuse the
    shared workflow and model clients, but give this worker booking and
    customer services that verify ownership inside each write transaction.
    If the lease expires and another worker takes over, the old attempt
    cannot change a customer's account or booking.

    Give the attempt its own place in Redis to save the conversation draft.
    The caller can load the last completed draft into that space and publish
    its reference when the turn finishes and the claim is still valid.

    If the registered bot's credentials are unusable, schedule a retry and
    return None so the caller stops before processing the message.
    """
    scoped_runtime = replace(
        runtime,
        bookings=BookingService(
            runtime.repository,
            turn_guard=claim.assert_owned,
        ),
    )
    repository = TelegramRepository(turns.database_url, turn_guard=claim.assert_owned)
    try:
        api = claim.bot.client()
    except RuntimeError:
        _defer_turn(turns, claim, runtime, "bot_credentials")
        return None

    conversation_id = _conversation_id(claim.bot.business_id, claim.bot.bot_id, claim.row["chat_id"])
    # Give every attempt a unique Redis thread, even for retries of the same message.
    checkpoint = f"{conversation_id}:attempt:{claim.generation}:{uuid.uuid4().hex}"
    return TelegramWorker(scoped_runtime, api, repository, claim.bot, checkpoint)


def _restore_turn_checkpoint(turns: TelegramTurns, claim: TurnClaim, worker: TelegramWorker) -> bool:
    """Load the previous draft so a reply like '1' refers to the right offer.

    An expired draft requires a fresh offer. A Redis outage defers execution
    so the worker can try to recover the draft on its next attempt.
    """
    if not claim.checkpoint_thread:
        return True
    try:
        if worker.runtime.workflow.graph.checkpointer.fork_completed(
            claim.checkpoint_thread, worker.checkpoint_thread
        ):
            return True
        turns.finish(claim, "Your previous offer expired. Please ask for available times again.", None)
    except RedisError:
        _defer_turn(turns, claim, worker.runtime, "checkpoint_unavailable")
    return False


def _defer_turn(turns: TelegramTurns, claim: TurnClaim, runtime: RuntimeContext, reason: str) -> None:
    """Reschedule a setup failure before the turn has made any customer or booking changes."""
    turns.defer_before_execution(claim, reason)
    log_event(runtime.logger, "telegram.turn.deferred", bot_id=claim.bot.bot_id, failure_type=reason)


@contextmanager
def _keep_turn_claim_alive(turns: TelegramTurns, claim: TurnClaim) -> Iterator[None]:
    """Keep the claim valid during inference, which can outlast the lease.

    Renew in the background while the turn blocks on models. Stop renewal
    when the turn exits, including when processing raises an error.
    """
    stop_renewal = Event()
    renewal = Thread(target=_renew_turn_claim, args=(turns, claim, stop_renewal), daemon=True)
    renewal.start()
    try:
        yield
    finally:
        stop_renewal.set()
        renewal.join(timeout=1)


def _renew_turn_claim(turns: TelegramTurns, claim: TurnClaim, stop: Event) -> None:
    """Refresh the lease while ownership holds.

    If renewal fails, booking and reply transactions still verify the claim
    before writing.
    """
    while not stop.wait(LEASE_SECONDS / 3):
        try:
            if not turns.renew(claim):
                return
        except DATABASE_ERRORS:
            return


class TelegramWorker:
    """Resolve customer identity and run one claimed text or voice turn."""

    def __init__(
        self, runtime, api: TelegramBotApi, repository: TelegramRepository,
        bot: TelegramBot, checkpoint_thread: str | None,
    ):
        self.runtime = runtime
        self.api = api
        self.repository = repository
        self.business_id = bot.business_id
        self.bot_id = bot.bot_id
        self.checkpoint_thread = checkpoint_thread

    def _ensure_customer(self, sender: dict, user_id: int) -> dict:
        """Resolve the verified sender to the customer identity used for booking ownership.

        The repository finds the link by business, bot, and Telegram user ID
        and creates a customer if needed. Profile names supply a display label.
        """
        username = _optional_text(sender.get("username"), 64)
        first_name = _optional_text(sender.get("first_name"), 120)
        display_name = f"@{username}" if username else first_name or "Telegram customer"
        return self.repository.ensure_customer_for_sender(
            self.business_id, self.bot_id, user_id, display_name, username
        )

    def handle_message(
        self, row: dict, message: dict, previous_checkpoint: str | None
    ) -> tuple[str, str | None]:
        """Handle commands and contacts directly and send scheduling input to the workflow.

        If processing leaves the booking outcome uncertain, ask the customer
        to check their appointments before retrying.
        """
        identity = self._ensure_customer(message["from"], row["telegram_user_id"])
        debug_event("telegram.turn.customer_resolved")
        text = message.get("text")
        if _is_start_command(text):
            debug_event("telegram.input.classified", kind="start")
            return (
                "Tell me which service and date you would like. "
                "You can type or send a voice message.",
                previous_checkpoint,
            )

        contact = message.get("contact")
        if isinstance(contact, dict):
            debug_event("telegram.input.classified", kind="contact")
            return self._contact_reply(identity, contact), previous_checkpoint

        try:
            return self._run_turn(row, identity, message, previous_checkpoint)
        except ProviderError as error:
            log_event(
                self.runtime.logger,
                "telegram.provider.failed",
                failure_type=type(error).__name__,
            )
            return (
                "The language service is unavailable. Please retry or send a shorter message. "
                "No booking change was made.",
                previous_checkpoint,
            )
        except DomainError as error:
            debug_event("telegram.input.domain_error", code=error.code)
            return error.message, previous_checkpoint
        except Exception as error:
            # A write may have committed before the failure. Do not promise rollback.
            log_event(
                self.runtime.logger,
                "telegram.turn.failed",
                failure_type=type(error).__name__,
            )
            return (
                "I couldn't confirm whether that request completed. "
                "Please check your appointments before trying again.",
                previous_checkpoint,
            )

    def _contact_reply(self, identity: dict, contact: dict) -> str:
        """Save a normalized phone number only when the shared contact belongs to the sender."""
        if contact.get("user_id") != identity["telegram_user_id"]:
            return "Share your own contact, or continue without a phone number."

        try:
            phone = canonical_phone(contact.get("phone_number", ""))
            self.repository.save_contact(
                self.business_id, identity["customer_id"], phone
            )
        except (DomainError, ValueError):
            return "That contact is invalid. You can continue without a phone number."
        return "Contact saved. What service and date would you like?"

    def _message_input(self, message: dict) -> str | bytes | None:
        """Keep Telegram file access and its download limit at the channel boundary."""
        text = message.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        media = message.get("voice") or message.get("audio")
        if not isinstance(media, dict):
            return None
        file_id = media.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("missing_audio_file")

        settings = self.runtime.settings
        duration = media.get("duration")
        with timed_event(
            "telegram.audio.download",
            audio_seconds=duration if type(duration) is int else None,
        ):
            audio = self.api.get_file(file_id, settings.telegram_audio_max_bytes)
            debug_event("telegram.audio.downloaded", bytes_received=len(audio))
        return audio

    def _run_turn(
        self, row: dict, identity: dict, message: dict, previous_checkpoint: str | None
    ) -> tuple[str, str | None]:
        """Run one scheduling message with a catalog and customer scoped to this turn.

        Transcription and the graph share the same active catalog. The Telegram
        update ID keeps booking retries tied to the original request.
        """
        conversation_id = _conversation_id(
            self.business_id, self.bot_id, identity["telegram_user_id"]
        )
        actions = CustomerSchedulingActions(
            runtime=self.runtime,
            business_id=self.business_id,
            customer_id=identity["customer_id"],
            conversation_id=conversation_id,
        )
        incoming = self._message_input(message)
        if incoming is None:
            debug_event("telegram.input.classified", kind="unsupported")
            return "Send text or a voice message to schedule an appointment.", previous_checkpoint
        recording = incoming if isinstance(incoming, bytes) else None
        modality = "audio" if recording is not None else "text"
        debug_event("telegram.input.classified", kind=modality)

        catalog = actions.catalog(self.business_id)
        turn = TurnContext(
            conversation_id=conversation_id,
            business_id=self.business_id,
            message=incoming if isinstance(incoming, str) else None,
            today=_today_in_timezone(catalog.business.timezone),
            customer_id=identity["customer_id"],
            idempotency_key=f"telegram:{self.bot_id}:{row['update_id']}",
            input_modality=modality,
            language=_telegram_language(message.get("from")),
        )
        result = self.runtime.workflow.handle_turn(
            turn,
            actions,
            checkpoint_thread=self.checkpoint_thread,
            recording=recording,
            display_name=identity.get("display_name"),
        )
        return result.text, self.checkpoint_thread or previous_checkpoint


def _today_in_timezone(timezone: str) -> str:
    """Use the business's local calendar day for phrases such as tomorrow."""
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def _telegram_language(sender: object) -> str:
    """Map Telegram's optional profile locale to the languages CAPY replies in."""
    if not isinstance(sender, dict):
        return "en"
    language_code = sender.get("language_code")
    if isinstance(language_code, str) and language_code.casefold().startswith("pt"):
        return "pt"
    return "en"


def _is_start_command(text: object) -> bool:
    """Recognize the welcome command even when Telegram appends a bot username."""
    if not isinstance(text, str) or not text.strip():
        return False
    command = text.split(maxsplit=1)[0].split("@", 1)[0]
    return command.lower() == "/start"


def _conversation_id(business_id: str, bot_id: int, user_id: int) -> str:
    """Hash the business, bot, and sender IDs to keep their conversation state separate."""
    scoped_sender = f"{business_id}:telegram:{bot_id}:{user_id}"
    return hashlib.sha256(scoped_sender.encode()).hexdigest()[:32]


def _optional_text(value, max_length: int) -> str | None:
    """Normalize and cap optional Telegram profile text before storage."""
    if not isinstance(value, str):
        return None
    result = " ".join(value.split())
    return result[:max_length] if result else None


def prune_history(database_url: str, limit: int = 500) -> None:
    """Delete old terminal records in bounded batches while retaining active jobs."""
    with database_session(database_url) as session:
        session.execute(
            text("""DELETE FROM telegram_inbox WHERE (bot_id,update_id) IN (
                   SELECT u.bot_id,u.update_id FROM telegram_inbox u
                   WHERE u.status='delivered' AND u.expires_at<clock_timestamp()
                     AND NOT EXISTS (SELECT 1 FROM telegram_outbox o
                         WHERE o.bot_id=u.bot_id AND o.update_id=u.update_id
                           AND o.status IN ('ready','dispatching'))
                   ORDER BY u.expires_at LIMIT :limit FOR UPDATE OF u SKIP LOCKED
               )"""),
            {"limit": limit},
        )
        session.execute(
            text("""DELETE FROM telegram_outbox WHERE id IN (
                   SELECT o.id FROM telegram_outbox o
                   WHERE o.finished_at<clock_timestamp()-interval '30 days'
                     AND o.status IN ('sent','failed','canceled','ambiguous')
                   ORDER BY o.finished_at LIMIT :limit FOR UPDATE OF o SKIP LOCKED
               )"""),
            {"limit": limit},
        )
