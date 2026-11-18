from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from app.channels.phone import canonical_phone
from app.channels.telegram import TelegramBotApi
from app.channels.telegram_repository import TelegramRepository
from app.customer_scheduling_actions import CustomerSchedulingActions
from app.graph.contracts import TurnContext
from app.llm.gemma import ProviderError
from app.models import DomainError
from app.observability import debug_event, log_event, timed_event


class TelegramWorker:
    """Resolve customer identity and run one claimed text or voice turn."""

    def __init__(self, runtime, api: TelegramBotApi, repository: TelegramRepository):
        """Reuse the shared runtime while keeping bot and checkpoint state local to a turn."""
        self.runtime = runtime
        self.api = api
        self.repository = repository
        self.business_id = runtime.settings.demo_business_id
        self.bot_id: int | None = None
        self.checkpoint_thread: str | None = None
        self.checkpoint_completed = False

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

    def _reply_to_message(self, row: dict, identity: dict, message: dict) -> str:
        """Handle commands and contacts directly and send scheduling input to the workflow.

        If processing leaves the booking outcome uncertain, ask the customer
        to check their appointments before retrying.
        """
        text = message.get("text")
        if _is_start_command(text):
            debug_event("telegram.input.classified", kind="start")
            return (
                "Tell me which service and date you would like. "
                "You can type or send a voice message."
            )

        # check wether this telegram message contains a shared contact card
        contact = message.get("contact")
        if isinstance(contact, dict):
            debug_event("telegram.input.classified", kind="contact")
            return self._contact_reply(identity, contact)

        try:
            return self._run_turn(row, identity, message)
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

    def _run_turn(self, row: dict, identity: dict, message: dict) -> str:
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
            return "Send text or a voice message to schedule an appointment."
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
        )
        result = self.runtime.workflow.handle_turn(
            turn,
            actions,
            checkpoint_thread=self.checkpoint_thread,
            recording=recording,
            display_name=identity.get("display_name"),
        )
        self.checkpoint_completed = self.checkpoint_thread is not None
        return result.text


def _today_in_timezone(timezone: str) -> str:
    """Use the business's local calendar day for phrases such as tomorrow."""
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


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
