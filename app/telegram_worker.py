from __future__ import annotations

import argparse
import hashlib
import time
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from app.channels.bots import BotRegistry
from app.channels.phone import canonical_phone
from app.channels.telegram import TelegramApiError, TelegramBotApi
from app.channels.telegram_intake import private_message as _private_message
from app.channels.telegram_repository import TelegramRepository
from app.config import load_settings
from app.customer_scheduling_actions import CustomerSchedulingActions
from app.graph.contracts import TurnContext
from app.llm.gemma import ProviderError
from app.models import DomainError
from app.observability import debug_event, log_event, timed_event
from app.privacy import sanitize_message
from app.runtime import close_runtime, create_runtime


class TelegramWorker:
    """Poll durable updates, process each claimed message, then deliver saved replies.

    Booking policy lives in the workflow/domain services. This worker owns the
    Telegram transport, trusted sender identity, and complete message input.
    """

    def __init__(self, runtime, api: TelegramBotApi, repository: TelegramRepository):
        self.runtime = runtime
        self.api = api
        self.repository = repository
        self.business_id = runtime.settings.demo_business_id
        self.bot_id: int | None = None
        self.checkpoint_thread: str | None = None
        self.checkpoint_completed = False

    def run(self) -> None:
        me = self.api.get_me()
        self.bot_id = me["id"]
        if self.api.has_webhook():
            raise RuntimeError(
                "Telegram has a webhook configured; remove it manually before polling."
            )
        if self.runtime.repository.business(self.business_id) is None:
            raise RuntimeError("The configured Telegram business does not exist.")

        lock = self.repository.acquire_worker_lock(self.bot_id)
        if lock is None:
            raise RuntimeError(
                "Another Telegram worker already holds the polling lock."
            )
        try:
            if BotRegistry(self.repository.database_url).contains(self.bot_id):
                raise RuntimeError("Use the separate Telegram service roles for a registered bot.")
            log_event(
                self.runtime.logger, "telegram.worker.started", bot_id=self.bot_id
            )
            self.repository.recover_interrupted(self.bot_id)
            offset = self.repository.poll_offset(self.bot_id)
            while True:
                self._deliver_ready_replies()
                self._process_pending_updates()
                self._deliver_ready_replies()
                self.repository.prune_receipts(self.bot_id)
                offset = self._poll_updates(offset)
        finally:
            lock.close()

    def _poll_updates(self, offset: int | None) -> int | None:
        """Advance the local offset only after the update is saved durably."""
        try:
            updates = self.api.get_updates(
                offset, self.runtime.settings.telegram_poll_seconds
            )
            for update in updates:
                update_id = update.get("update_id")
                if not isinstance(update_id, int):
                    continue
                self._save_update(update_id, update)
                offset = max(offset or 0, update_id + 1)
        except TelegramApiError as error:
            log_event(
                self.runtime.logger,
                "telegram.poll.failed",
                failure_type=error.code,
            )
            time.sleep(min(max(error.retry_after or 2, 1), 300))
        return offset

    def _save_update(self, update_id: int, update: dict) -> None:
        message = update.get("message")
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = message.get("from") if isinstance(message, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        user_id = sender.get("id") if isinstance(sender, dict) else None
        chat_id = chat_id if isinstance(chat_id, int) else None
        user_id = user_id if isinstance(user_id, int) else None
        self.repository.save_update(
            bot_id=self.bot_id,
            update_id=update_id,
            business_id=self.business_id,
            chat_id=chat_id,
            telegram_user_id=user_id,
            payload=(
                {"message": message} if isinstance(message, dict) else {"ignored": True}
            ),
        )

    def _process_pending_updates(self) -> None:
        for pending in self.repository.pending_updates(self.bot_id):
            claimed = self.repository.claim_update(self.bot_id, pending["update_id"])
            if claimed is None:
                continue
            self._process_one(claimed)

    def _process_one(self, row: dict) -> None:
        """Handle one claimed update and persist its reply before delivery."""
        message = _private_message(row)
        if message is None:
            self.repository.mark_ignored(self.bot_id, row["update_id"])
            return

        identity = self._ensure_customer(message["from"], row["telegram_user_id"])
        reply = self._reply_to_message(row, identity, message)
        self.repository.save_reply(self.bot_id, row["update_id"], reply)

    def _ensure_customer(self, sender: dict, user_id: int) -> dict:
        username = _optional_text(sender.get("username"), 64)
        first_name = _optional_text(sender.get("first_name"), 120)
        display_name = f"@{username}" if username else first_name or "Telegram customer"
        return self.repository.ensure_customer_for_sender(
            self.business_id, self.bot_id, user_id, display_name, username
        )

    def _reply_to_message(self, row: dict, identity: dict, message: dict) -> str:
        text = message.get("text")
        if _is_start_command(text):
            debug_event("telegram.input.classified", kind="start")
            return (
                "Tell me which service and date you would like. "
                "You can type or send a voice message."
            )

        contact = message.get("contact")
        if isinstance(contact, dict):
            debug_event("telegram.input.classified", kind="contact")
            return self._contact_reply(identity, contact)

        try:
            incoming = self._message_input(message)
            if incoming is None:
                debug_event("telegram.input.classified", kind="unsupported")
                return "Send text or a voice message to schedule an appointment."
            content, modality = incoming
            debug_event("telegram.input.classified", kind=modality)
            if not content.strip():
                raise ValueError("empty_input")
            return self._run_turn(row, identity, content, modality)
        except ProviderError as error:
            log_event(
                self.runtime.logger,
                "telegram.provider.failed",
                failure_type=type(error).__name__,
            )
            return (
                "The language service is unavailable. Please retry or send a shorter message. "
                "No booking change was made."
            )
        except DomainError as error:
            debug_event("telegram.input.domain_error", code=error.code)
            return error.message
        except Exception as error:
            # A write may have committed before the failure. Do not promise rollback.
            log_event(
                self.runtime.logger,
                "telegram.turn.failed",
                failure_type=type(error).__name__,
            )
            return (
                "I couldn't confirm whether that request completed. "
                "Please check your appointments before trying again."
            )

    def _contact_reply(self, identity: dict, contact: dict) -> str:
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

    def _message_input(self, message: dict) -> tuple[str, str] | None:
        """Return text or a complete audio transcript; never a partial recording."""
        text = message.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip(), "text"

        media = message.get("voice") or message.get("audio")
        if not isinstance(media, dict):
            return None
        file_id = media.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("missing_audio_file")

        settings = self.runtime.settings
        duration = media.get("duration")
        with timed_event("telegram.audio.prepared", audio_seconds=duration if type(duration) is int else None):
            audio = self.api.get_file(file_id, settings.telegram_audio_max_bytes)
            debug_event("telegram.audio.downloaded", bytes_received=len(audio))
            transcript = self.runtime.audio_understanding.transcribe_recording(
                audio,
                self._audio_transcription_context(),
                chunker=self.runtime.audio_chunker,
                processing_timeout_seconds=settings.telegram_audio_processing_timeout_seconds,
            )
        return transcript, "audio"

    def _audio_transcription_context(self) -> dict:
        """Give Gemma active catalog names and the local date to interpret speech."""
        catalog = self.runtime.repository.catalog(self.business_id)
        if catalog is None:
            raise DomainError(
                "business_not_found", "The business is not available.", 404
            )
        return {
            "service_names": [service.name for service in catalog.services],
            "professional_names": [person.name for person in catalog.professionals],
            "today": _today_in_timezone(catalog.business.timezone),
        }

    def _business_local_date(self) -> str:
        """Resolve relative dates in the business timezone using only its row.

        The graph loads the catalog when it needs the services and professionals.
        """
        business = self.runtime.repository.business(self.business_id)
        if business is None:
            raise DomainError(
                "business_not_found", "The business is not available.", 404
            )
        return _today_in_timezone(business.timezone)

    def _run_turn(self, row: dict, identity: dict, message: str, modality: str) -> str:
        """Run the workflow with the trusted sender and a stable key for retries."""
        conversation_id = _conversation_id(
            self.business_id, self.bot_id, identity["telegram_user_id"]
        )
        actions = CustomerSchedulingActions(
            runtime=self.runtime,
            business_id=self.business_id,
            customer_id=identity["customer_id"],
            conversation_id=conversation_id,
        )
        turn = TurnContext(
            conversation_id=conversation_id,
            business_id=self.business_id,
            message=sanitize_message(message, identity.get("display_name")),
            today=self._business_local_date(),
            customer_id=identity["customer_id"],
            idempotency_key=f"telegram:{self.bot_id}:{row['update_id']}",
            input_modality=modality,
        )
        if self.checkpoint_thread:
            result = self.runtime.workflow.handle_turn(
                turn, actions, checkpoint_thread=self.checkpoint_thread
            )
            self.checkpoint_completed = True
        else:
            result = self.runtime.workflow.handle_turn(turn, actions)
        return result.text

    def _deliver_ready_replies(self) -> None:
        for row in self.repository.ready_replies(self.bot_id):
            try:
                if row["reply_text"]:
                    self.api.send_message(
                        row["chat_id"], row["reply_text"], row["reply_markup"]
                    )
                self.repository.mark_delivered(self.bot_id, row["update_id"])
            except TelegramApiError as error:
                if error.is_permanent_rejection:
                    self.repository.discard_reply(
                        self.bot_id, row["update_id"], error.code
                    )
                    log_event(
                        self.runtime.logger,
                        "telegram.reply.discarded",
                        failure_type=error.code,
                    )
                    continue
                delay = _reply_retry_delay(row["attempts"], error.retry_after)
                self.repository.delay_reply(
                    self.bot_id, row["update_id"], delay, error.code
                )
                log_event(
                    self.runtime.logger,
                    "telegram.reply.failed",
                    failure_type=error.code,
                )
                # Preserve delivery order when Telegram is rate limiting or
                # temporarily unavailable; the next poll cycle retries it.
                break


def _today_in_timezone(timezone: str) -> str:
    """Use the business's local calendar day for phrases such as tomorrow."""
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def _reply_retry_delay(attempts: int, retry_after: int | None) -> int:
    if retry_after:
        return retry_after
    # Cap the exponent as well as the delay for repeatedly failing deliveries.
    return min(300, 2 ** min(attempts + 1, 8))


def _is_start_command(text: object) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    command = text.split(maxsplit=1)[0].split("@", 1)[0]
    return command.lower() == "/start"


def _conversation_id(business_id: str, bot_id: int, user_id: int) -> str:
    scoped_sender = f"{business_id}:telegram:{bot_id}:{user_id}"
    return hashlib.sha256(scoped_sender.encode()).hexdigest()[:32]


def _optional_text(value, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    result = " ".join(value.split())
    return result[:max_length] if result else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Telegram scheduling bot")
    parser.add_argument("--poll-seconds", type=int, default=None)
    args = parser.parse_args()
    settings = load_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting the Telegram worker.")
    if args.poll_seconds is not None:
        if not 1 <= args.poll_seconds <= 50:
            raise SystemExit("--poll-seconds must be between 1 and 50")
        settings = replace(settings, telegram_poll_seconds=args.poll_seconds)
    runtime = create_runtime(settings)
    api = TelegramBotApi(settings.telegram_bot_token)
    repository = TelegramRepository(settings.database_url)
    try:
        TelegramWorker(runtime, api, repository).run()
    finally:
        close_runtime(runtime)


if __name__ == "__main__":
    main()
