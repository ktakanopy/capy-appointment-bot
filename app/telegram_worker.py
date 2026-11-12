from __future__ import annotations

import argparse
import hashlib
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.channels.telegram import TelegramApiError, TelegramBotApi
from app.channels.telegram_repository import TelegramRepository
from app.privacy import sanitize_message
from app.llm.gemma import ProviderError
from app.chat_ports import ChatPorts
from app.config import load_settings
from app.graph.contracts import TurnContext
from app.models import DomainError
from app.observability import log_event
from app.runtime import close_runtime, create_runtime
from app.channels.phone import canonical_phone


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
        message = (row.get("payload") or {}).get("message")
        if not isinstance(message, dict):
            self.repository.mark_ignored(self.bot_id, update_id)
            return
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        user_id = row.get("telegram_user_id")
        if (chat.get("type") != "private" or not isinstance(row.get("chat_id"), int)
                or sender.get("is_bot") or not isinstance(user_id, int) or sender.get("id") != user_id):
            self.repository.mark_ignored(self.bot_id, update_id)
            return
        username = _optional_text(sender.get("username"), 64)
        name = "@" + username if username else _optional_text(sender.get("first_name"), 120) or "Telegram customer"
        identity = self.repository.ensure_identity(self.business_id, self.bot_id, user_id, name, username)
        text = message.get("text")
        if isinstance(text, str) and text.strip().split()[0].split("@")[0].lower() == "/start":
            self.repository.save_reply(self.bot_id, update_id, "Tell me which service and date you would like. You can type or send a voice message.")
            return
        contact = message.get("contact")
        if isinstance(contact, dict):
            if contact.get("user_id") != user_id:
                reply = "Share your own contact, or continue without a phone number."
            else:
                try:
                    phone = canonical_phone(contact.get("phone_number", ""))
                    self.repository.save_contact(self.business_id, identity["customer_id"], phone)
                    reply = "Contact saved. What service and date would you like?"
                except (DomainError, ValueError):
                    reply = "That contact is invalid. You can continue without a phone number."
            self.repository.save_reply(self.bot_id, update_id, reply)
            return
        try:
            modality = "text"
            if isinstance(text, str) and text.strip():
                incoming = text.strip()
            elif isinstance(message.get("voice") or message.get("audio"), dict):
                modality = "audio"
                media = message.get("voice") or message.get("audio")
                file_id = media.get("file_id")
                if not isinstance(file_id, str) or not file_id:
                    raise ValueError("missing_audio_file")
                audio = self.api.get_file(file_id, self.runtime.settings.telegram_audio_max_bytes)
                incoming = self.runtime.audio_understanding.transcribe_recording(
                    audio, self._interpretation_context(identity), chunker=self.runtime.audio_chunker,
                    processing_timeout_seconds=self.runtime.settings.telegram_audio_processing_timeout_seconds,
                )
            else:
                self.repository.save_reply(self.bot_id, update_id, "Send text or a voice message to schedule an appointment.")
                return
            if not incoming.strip():
                raise ValueError("empty_input")
            reply = self._run_turn(row, identity, incoming, modality)
        except ProviderError as error:
            log_event(self.runtime.logger, "telegram.provider.failed", failure_type=type(error).__name__)
            reply = "The language service is unavailable. Please retry or send a shorter message. No booking change was made."
        except DomainError as error:
            reply = error.message
        except Exception as error:
            log_event(self.runtime.logger, "telegram.turn.failed", failure_type=type(error).__name__)
            reply = "I couldn't confirm whether that request completed. Please check your appointments before trying again."
        self.repository.save_reply(self.bot_id, update_id, reply)

    def _interpretation_context(self, identity: dict) -> dict:
        catalog = self.runtime.repository.catalog(self.business_id)
        if catalog is None:
            raise DomainError("business_not_found", "The business is not available.", 404)
        return {
            "service_names": [s.name for s in catalog.services],
            "professional_names": [p.name for p in catalog.professionals],
            "today": datetime.now(ZoneInfo(catalog.business.timezone)).date().isoformat(),
        }

    def _run_turn(self, row: dict, identity: dict, message: str, modality: str) -> str:
        context = self._interpretation_context(identity)
        conversation_id = _conversation_id(self.business_id, self.bot_id, identity["telegram_user_id"])
        ports = ChatPorts(self.runtime, self.business_id, identity["customer_id"], conversation_id)
        result = self.runtime.workflow.handle_turn(TurnContext(
            conversation_id=conversation_id, business_id=self.business_id,
            message=sanitize_message(message, identity.get("display_name")), today=context["today"],
            customer_id=identity["customer_id"], idempotency_key=f"telegram:{self.bot_id}:{row['update_id']}",
            input_modality=modality,
        ), ports)
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
    return hashlib.sha256(f"{business_id}:telegram:{bot_id}:{user_id}".encode()).hexdigest()[:32]


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
