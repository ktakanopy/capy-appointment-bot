from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from app.chat_adapter import conversation_id_for_sender, redact_token
from app.graph.contracts import TurnContext
from app.models import CustomerInput, DomainError


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def verify_subscription(mode: str | None, token: str | None, challenge: str | None, expected_token: str | None) -> str | None:
    if mode == "subscribe" and expected_token and token == expected_token and challenge:
        return challenge
    return None


def canonical_phone(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if not 7 <= len(digits) <= 20:
        raise DomainError("invalid_phone", "Phone number is not valid.", 422)
    return f"+{digits}"


@dataclass
class InboundText:
    message_id: str
    sender: str
    phone_number_id: str
    text: str


def parse_inbound_texts(payload: dict) -> list[InboundText]:
    messages: list[InboundText] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = str(value.get("metadata", {}).get("phone_number_id", ""))
            for item in value.get("messages", []):
                if item.get("type") != "text":
                    continue
                body = item.get("text", {}).get("body")
                if not item.get("id") or not item.get("from") or not isinstance(body, str):
                    continue
                messages.append(InboundText(item["id"], item["from"], phone_number_id, body))
    return messages


class MemoryDeduper:
    def __init__(self):
        self.seen: set[str] = set()

    def claim(self, message_id: str) -> bool:
        if message_id in self.seen:
            return False
        self.seen.add(message_id)
        return True

    def release(self, message_id: str) -> None:
        self.seen.discard(message_id)


class MemoryOutbox:
    def __init__(self):
        self.messages: list[dict[str, str]] = []

    def add(self, recipient: str, text: str, message_id: str) -> None:
        self.messages.append({"to": recipient, "text": text, "in_reply_to": message_id})


def process_inbound(runtime, body: bytes, signature: str | None) -> tuple[int, dict | str]:
    settings = runtime.settings
    secret = getattr(settings, "whatsapp_app_secret", None)
    if not secret:
        raise DomainError("whatsapp_not_configured", "WhatsApp is not configured.", 503)
    if not verify_signature(secret, body, signature):
        raise DomainError("invalid_signature", "Invalid webhook signature.", 401)
    payload = json.loads(body.decode() or "{}")
    phone_number_id = getattr(settings, "whatsapp_phone_number_id", None)
    business_id = getattr(settings, "demo_business_id", None)
    deduper = getattr(runtime, "whatsapp_deduper", None) or MemoryDeduper()
    outbox = getattr(runtime, "whatsapp_outbox", None) or MemoryOutbox()
    runtime.whatsapp_deduper = deduper
    runtime.whatsapp_outbox = outbox
    handled = 0
    for message in parse_inbound_texts(payload):
        if phone_number_id and message.phone_number_id != phone_number_id:
            continue
        if not deduper.claim(message.message_id):
            continue
        try:
            _handle_message(runtime, business_id, message, outbox)
            handled += 1
        except Exception:
            deduper.release(message.message_id)
            raise
    return 200, {"received": handled}


def _handle_message(runtime, business_id: str, message: InboundText, outbox: MemoryOutbox) -> None:
    from app.chat_ports import ChatPorts
    from app.models import ChatRequest

    sender = canonical_phone(message.sender)
    conversation_id = conversation_id_for_sender(business_id, "whatsapp", sender)
    customer_id = None
    customer = None
    repository = runtime.repository
    if hasattr(repository, "customer_by_phone"):
        found = repository.customer_by_phone(business_id, sender)
        if found is not None:
            customer_id = found.id
            customer = CustomerInput(full_name=found.full_name, phone=found.phone)
    store = getattr(runtime, "chat_store", None)
    if store is not None and hasattr(store, "create_session"):
        try:
            store.require_session(conversation_id)
        except DomainError:
            store.create_session(conversation_id, "")
    safe_text = redact_token(message.text)
    request = ChatRequest(session_id=conversation_id, business_id=business_id, message=safe_text)
    ports = ChatPorts(runtime, request, None, customer, customer_id)
    catalog = ports.catalog(business_id)
    from app.chat_adapter import local_today

    result = runtime.workflow.handle_turn(
        TurnContext(
            conversation_id=conversation_id,
            business_id=business_id,
            message=safe_text,
            today=local_today(catalog).isoformat(),
            auth_kind="customer" if customer_id else "anonymous",
            customer_id=customer_id,
            customer_ready=customer is not None,
        ),
        ports,
    )
    outbox.add(sender, result.text, message.message_id)
