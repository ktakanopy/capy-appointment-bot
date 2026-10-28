from __future__ import annotations

import argparse
import re
import time
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from app.channels.telegram import TelegramApiError, TelegramBotApi, contact_keyboard
from app.channels.telegram_repository import TelegramRepository
from app.chat_adapter import conversation_id_for_sender, redact_token
from app.chat_ports import ChatPorts
from app.config import load_settings
from app.graph.contracts import TurnContext
from app.graph.decision import normalized_from_extraction
from app.models import ChatRequest, CustomerInput, DomainError
from app.observability import log_event
from app.runtime import close_runtime, create_runtime
from app.channels.whatsapp import canonical_phone


_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{5,22}\d(?!\w)")
_DATE_CANDIDATE = re.compile(r"\b(?:20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/20\d{2})\b")
_EXPLICIT_NAME = re.compile(
    r"\b(?:my (?:full )?name is|me chamo|"
    r"meu nome (?:completo )?(?:é|eh)|nome completo é)\s+"
    r"(.+?)(?:[,;.!?]|\s+(?:and|i want|i would like|i'd like|book|schedule|quero|gostaria|para|e quero)\b|$)",
    re.IGNORECASE,
)
_NON_NAME_WORDS = {
    "and", "appointment", "book", "cancel", "confirm", "gostaria", "help",
    "horario", "i", "need", "please", "quero", "remarcar", "schedule",
    "today", "tomorrow", "want", "what", "when", "would",
}
_CONTACT_MISMATCH = (
    "Please use the button to share your own phone number so I can verify your account."
)


class TelegramWorker:
    def __init__(self, runtime, api: TelegramBotApi, repository: TelegramRepository):
        self.runtime = runtime
        self.api = api
        self.repository = repository
        self.business_id = runtime.settings.demo_business_id
        self.bot_id: int | None = None

    def run(self) -> None:
        me = self.api.get_me()
        self.bot_id = me["id"]
        if self.api.has_webhook():
            raise RuntimeError("Telegram has a webhook configured; remove it manually before polling.")
        if self.runtime.repository.business(self.business_id) is None:
            raise RuntimeError("The configured Telegram business does not exist.")

        lock = self.repository.acquire_worker_lock(self.bot_id)
        if lock is None:
            raise RuntimeError("Another Telegram worker already holds the polling lock.")
        try:
            log_event(self.runtime.logger, "telegram.worker.started", bot_id=self.bot_id)
            self.repository.recover_interrupted(self.bot_id)
            offset = self.repository.poll_offset(self.bot_id)
            while True:
                self._deliver_ready_replies()
                self._process_pending_updates()
                self._deliver_ready_replies()
                self.repository.prune_receipts(self.bot_id)
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
        finally:
            lock.close()

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
            payload={"message": message} if isinstance(message, dict) else {"ignored": True},
        )

    def _process_pending_updates(self) -> None:
        for pending in self.repository.pending_updates(self.bot_id):
            claimed = self.repository.claim_update(self.bot_id, pending["update_id"])
            if claimed is None:
                continue
            self._process_one(claimed)

    def _process_one(self, row: dict) -> None:
        update_id = row["update_id"]
        if (row.get("payload") or {}).get("ignored"):
            self.repository.mark_ignored(self.bot_id, update_id)
            return
        message = (row.get("payload") or {}).get("message")
        if not isinstance(message, dict):
            self.repository.mark_ignored(self.bot_id, update_id)
            return
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if (
            chat.get("type") != "private"
            or not isinstance(row.get("chat_id"), int)
            or sender.get("is_bot")
        ):
            self.repository.mark_ignored(self.bot_id, update_id)
            return

        user_id = row["telegram_user_id"]
        if not isinstance(user_id, int):
            self.repository.mark_ignored(self.bot_id, update_id)
            return
        self.repository.save_username(
            self.business_id,
            self.bot_id,
            user_id,
            _optional_text(sender.get("username"), 64),
        )
        identity = self.repository.identity(self.business_id, self.bot_id, user_id)
        if identity is None:
            self.repository.save_reply(self.bot_id, update_id, _CONTACT_MISMATCH, contact_keyboard())
            return
        if (
            identity.get("verified_phone")
            and not identity.get("customer_id")
            and self.repository.link_existing_customer(self.business_id, self.bot_id, user_id)
        ):
            identity = self.repository.identity(self.business_id, self.bot_id, user_id)

        contact = message.get("contact")
        if isinstance(contact, dict):
            self._process_contact(row, message, contact)
            return

        text = message.get("text")
        voice = message.get("voice")
        audio = message.get("audio")
        command = text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower() if isinstance(text, str) and text.strip() else ""
        if command == "/start":
            name = identity.get("verified_name")
            if not name and identity.get("username"):
                name = f"@{identity['username']}"
            greeting = f"Hi {name}! " if name else "Hi! "
            if identity.get("verified_phone"):
                self.repository.save_reply(
                    self.bot_id,
                    update_id,
                    greeting + "Tell me what service and day you would like.",
                    {"remove_keyboard": True},
                )
            else:
                self.repository.save_reply(
                    self.bot_id,
                    update_id,
                    greeting + "Please share your phone number to verify your account.",
                    contact_keyboard(),
                )
            return

        # Require a Telegram self-contact before processing any conversation.
        # This prevents a typed/spoken phone from claiming an existing account.
        if not identity.get("verified_phone"):
            self.repository.save_reply(
                self.bot_id,
                update_id,
                "Please share your own phone number first, then send your message again.",
                contact_keyboard(),
            )
            return

        if isinstance(text, str) and text.strip():
            incoming = redact_token(text.strip())
            modality = "text"
        elif isinstance(voice, dict) or isinstance(audio, dict):
            media = voice if isinstance(voice, dict) else audio
            file_id = media.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                self.repository.save_reply(self.bot_id, update_id, "I couldn't read that audio. Please try again or send text.")
                return
            try:
                audio_bytes = self.api.get_file(file_id, self.runtime.settings.telegram_audio_max_bytes)
                incoming = self.runtime.audio_understanding.transcribe_recording(
                    audio_bytes,
                    self._interpretation_context(identity),
                    chunker=self.runtime.audio_chunker,
                    processing_timeout_seconds=self.runtime.settings.telegram_audio_processing_timeout_seconds,
                )
            except Exception as error:
                # Do not log provider errors, file paths, audio, or transcripts.
                log_event(
                    self.runtime.logger,
                    "telegram.audio.failed",
                    failure_type=type(error).__name__,
                )
                self.repository.save_reply(
                    self.bot_id,
                    update_id,
                    "I couldn't understand the complete recording. Please try again, send a shorter recording, or type your request.",
                )
                return
            incoming = redact_token(incoming.strip())
            modality = "audio"
        else:
            self.repository.save_reply(
                self.bot_id,
                update_id,
                "Send a text message or a Telegram voice message and I'll help with your appointment.",
            )
            return

        if not incoming:
            self.repository.save_reply(self.bot_id, update_id, "I couldn't read that message. Please try again.")
            return
        try:
            reply = self._run_turn(row, identity, incoming, modality)
            self.repository.save_reply(self.bot_id, update_id, reply, {"remove_keyboard": True})
        except Exception as error:
            log_event(
                self.runtime.logger,
                "telegram.turn.failed",
                failure_type=type(error).__name__,
            )
            self.repository.save_reply(
                self.bot_id,
                update_id,
                "I couldn't confirm whether that request completed. "
                "Please check your appointments before submitting it again.",
            )

    def _process_contact(self, row: dict, message: dict, contact: dict) -> None:
        update_id = row["update_id"]
        user_id = row["telegram_user_id"]
        contact_user_id = contact.get("user_id")
        if not isinstance(contact_user_id, int) or contact_user_id != user_id:
            self.repository.save_reply(self.bot_id, update_id, _CONTACT_MISMATCH, contact_keyboard())
            return
        phone_text = contact.get("phone_number")
        first_name = _optional_text(contact.get("first_name"), 80)
        last_name = _optional_text(contact.get("last_name"), 80)
        full_name = " ".join(part for part in (first_name, last_name) if part)
        try:
            phone = canonical_phone(phone_text) if isinstance(phone_text, str) else None
        except DomainError:
            phone = None
        try:
            name = CustomerInput(full_name=full_name, phone=phone).full_name if phone else None
        except ValueError:
            name = None
        if phone is None:
            self.repository.save_reply(
                self.bot_id, update_id,
                "I couldn't verify that phone number. Please use Telegram's Share my phone number button.",
                contact_keyboard(),
            )
            return

        outcome = self.repository.verify_contact(
            self.business_id, self.bot_id, user_id, phone, name
        )
        if outcome == "already_linked":
            reply = "This phone number or Telegram account is already linked elsewhere. Please contact the business for help."
        elif outcome == "identity_missing":
            reply = _CONTACT_MISMATCH
        elif name:
            reply = f"Thanks, {name}. Your phone is verified. Tell me what service and day you would like."
        else:
            reply = "Your phone is verified. Please send your full name and the service you would like."
        markup = {"remove_keyboard": True} if outcome in {"verified", "linked"} else contact_keyboard()
        self.repository.save_reply(self.bot_id, update_id, reply, markup)

    def _interpretation_context(self, identity: dict) -> dict:
        conversation_id = _conversation_id(
            self.business_id, self.bot_id, identity["telegram_user_id"]
        )
        catalog = self.runtime.repository.catalog(self.business_id)
        if catalog is None:
            raise DomainError("business_not_found", "The business is not available.", 404)
        return {
            "conversation_id": conversation_id,
            "service_names": [service.name for service in catalog.services],
            "professional_names": [person.name for person in catalog.professionals],
            "today": datetime.now(ZoneInfo(catalog.business.timezone)).date().isoformat(),
        }

    def _run_turn(self, row: dict, identity: dict, message: str, modality: str) -> str:
        user_id = identity["telegram_user_id"]
        conversation_id = _conversation_id(self.business_id, self.bot_id, user_id)
        try:
            self.runtime.chat_store.require_session(conversation_id)
        except DomainError:
            self.runtime.chat_store.create_session(conversation_id, "")

        catalog = self.runtime.repository.catalog(self.business_id)
        if catalog is None:
            raise DomainError("business_not_found", "The business is not available.", 404)
        draft = self.runtime.workflow.draft(conversation_id)
        history = self.runtime.workflow.messages(conversation_id)
        slots = self.runtime.chat_store.load_slots(conversation_id)
        explicit_name = _explicit_name(message)
        if explicit_name is None and draft.missing == "customer":
            explicit_name = _plain_name_answer(message)
        verified_name = identity.get("verified_name")
        private_name = verified_name or explicit_name
        interpretation_message = _redact_contact_details(message, private_name, None)
        if explicit_name and explicit_name != private_name:
            interpretation_message = _redact_contact_details(
                interpretation_message, explicit_name, None
            )
        extraction = self.runtime.interpreter.interpret(
            interpretation_message,
            {
                "service_names": [service.name for service in catalog.services],
                "professional_names": [person.name for person in catalog.professionals],
                "today": datetime.now(ZoneInfo(catalog.business.timezone)).date().isoformat(),
                "has_slots": bool(slots),
                "history": history,
            },
        )
        # Telegram's verified phone and the user's explicit name are collected
        # outside the model. Do not trust model-inferred contact fields.
        customer_name = private_name
        if customer_name and identity.get("verified_phone") and not identity.get("verified_name"):
            try:
                customer_name = CustomerInput(
                    full_name=customer_name,
                    phone=identity["verified_phone"],
                ).full_name
            except ValueError:
                customer_name = None
            if customer_name:
                self.repository.save_verified_name(
                    self.business_id, self.bot_id, user_id, customer_name
                )
                identity["verified_name"] = customer_name
        safe_message = _redact_contact_details(message, customer_name, extraction.phone)
        if explicit_name and explicit_name != customer_name:
            safe_message = _redact_contact_details(
                safe_message, explicit_name, extraction.phone
            )

        customer_id = identity.get("customer_id")
        customer = None
        if customer_id:
            stored = self.runtime.repository.customer(self.business_id, customer_id)
            if stored is None:
                raise DomainError("telegram_identity_invalid", "Account could not be verified.", 403)
            customer = CustomerInput(full_name=stored.full_name, phone=stored.phone)
        elif identity.get("verified_phone") and identity.get("verified_name"):
            try:
                customer = CustomerInput(
                    full_name=identity["verified_name"],
                    phone=identity["verified_phone"],
                )
            except ValueError:
                customer = None

        request = ChatRequest(
            session_id=conversation_id,
            business_id=self.business_id,
            message=safe_message or "Scheduling request",
            customer=customer,
        )
        ports = ChatPorts(
            self.runtime,
            request,
            None,
            customer,
            customer_id,
            verified_phone=identity.get("verified_phone"),
        )
        if customer_name:
            ports.capture_customer(customer_name, None)

        normalized = normalized_from_extraction(
            extraction,
            conversation_id=conversation_id,
            customer_id=customer_id,
            transcript=safe_message,
            modality=modality,
            draft=draft,
        )
        result = self.runtime.workflow.handle_turn(
            TurnContext(
                conversation_id=conversation_id,
                business_id=self.business_id,
                message=safe_message or "Scheduling request",
                today=datetime.now(ZoneInfo(catalog.business.timezone)).date().isoformat(),
                auth_kind="customer" if customer_id else "anonymous",
                customer_id=customer_id,
                customer_ready=ports.customer_ready(),
                idempotency_key=f"telegram:{self.bot_id}:{row['update_id']}",
                input_modality=modality,
                normalized=normalized,
                draft=draft,
            ),
            ports,
        )
        if result.booking and identity.get("verified_phone"):
            self.repository.attach_customer(
                self.business_id,
                self.bot_id,
                user_id,
                result.booking.customer.id,
                result.booking.customer.phone,
                result.booking.customer.full_name,
            )
        return _safe_reply(result.text)

    def _deliver_ready_replies(self) -> None:
        for row in self.repository.ready_replies(self.bot_id):
            try:
                if row["reply_text"]:
                    self.api.send_message(
                        row["chat_id"], row["reply_text"], row["reply_markup"]
                    )
                self.repository.mark_delivered(self.bot_id, row["update_id"])
            except TelegramApiError as error:
                if error.code in {
                    "telegram_api_400",
                    "telegram_api_403",
                    "telegram_http_400",
                    "telegram_http_403",
                }:
                    self.repository.discard_reply(
                        self.bot_id, row["update_id"], error.code
                    )
                    log_event(
                        self.runtime.logger,
                        "telegram.reply.discarded",
                        failure_type=error.code,
                    )
                    continue
                delay = error.retry_after or min(300, 2 ** min(row["attempts"] + 1, 8))
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


def _conversation_id(business_id: str, bot_id: int, user_id: int) -> str:
    return conversation_id_for_sender(business_id, "telegram", f"{bot_id}:{user_id}")


def _optional_text(value, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    result = " ".join(value.split())
    return result[:max_length] if result else None


def _explicit_name(message: str) -> str | None:
    match = _EXPLICIT_NAME.search(message)
    if not match:
        return None
    name = " ".join(match.group(1).split()).strip(" :")
    return name[:120] or None


def _plain_name_answer(message: str) -> str | None:
    """Accept a plain full name only when the graph explicitly asked for it."""
    candidate = " ".join(message.split()).strip(" ,;:.!?")
    words = candidate.split()
    if not 2 <= len(words) <= 5 or len(candidate) > 120:
        return None
    if any(word.casefold() in _NON_NAME_WORDS for word in words):
        return None
    if not all(
        all(character.isalpha() or character in "'’-" for character in word)
        for word in words
    ):
        return None
    return candidate


def _redact_contact_details(text: str, customer_name: str | None, phone: str | None) -> str:
    safe = redact_token(text)
    if customer_name:
        safe = re.sub(re.escape(customer_name), "[customer]", safe, flags=re.IGNORECASE)
    expected_digits = "".join(character for character in phone or "" if character.isdigit())
    dates = []
    for index, match in enumerate(reversed(list(_DATE_CANDIDATE.finditer(safe)))):
        marker = f"⟪date{index}⟫"
        dates.append((marker, match.group()))
        safe = safe[:match.start()] + marker + safe[match.end():]
    for match in reversed(list(_PHONE_CANDIDATE.finditer(safe))):
        candidate_digits = "".join(character for character in match.group() if character.isdigit())
        if len(candidate_digits) >= 7 or candidate_digits == expected_digits:
            safe = safe[:match.start()] + "[phone]" + safe[match.end():]
    for marker, original in dates:
        safe = safe.replace(marker, original)
    return safe


def _safe_reply(text: str) -> str:
    result = redact_token(text).strip()
    if "[redacted-token]" in result:
        return "For your privacy, I can't send booking credentials in chat. Please contact the business for help."
    if re.search(r"private management token is shown in the booking details", result, re.I):
        return "Your booking is complete. You can manage it here by telling me what you would like to do next."
    if re.search(r"management token in the booking controls|web booking controls", result, re.I):
        return "I couldn't verify an appointment linked to this Telegram account. Please contact the business for help."
    return result or "How can I help with your appointment?"


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
