from __future__ import annotations

import hashlib
import hmac
import json

from app.channels.whatsapp import MemoryDeduper, MemoryOutbox, canonical_phone
from app.chat_adapter import conversation_id_for_sender, local_today, redact_token
from app.graph.contracts import TurnContext
from app.models import ChatRequest, CustomerInput, DomainError


def verify_sms_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)


def process_sms(runtime, body: bytes, signature: str | None) -> tuple[int, dict]:
    """Accept one verified SMS and run the same workflow as WhatsApp text.

    The sender number identifies the customer. The message is text, so Gemma
    understands it through the normal interpreter path.
    """
    settings = runtime.settings
    secret = getattr(settings, "sms_webhook_secret", None)
    if not secret:
        raise DomainError("sms_not_configured", "SMS is not configured.", 503)
    if not verify_sms_signature(secret, body, signature):
        raise DomainError("invalid_signature", "Invalid SMS signature.", 401)
    payload = json.loads(body.decode() or "{}")
    message_id = str(payload.get("message_id") or "")
    sender = str(payload.get("from") or "")
    text = payload.get("text")
    if not message_id or not sender or not isinstance(text, str):
        raise DomainError("invalid_sms", "SMS payload is incomplete.", 422)
    deduper = getattr(runtime, "sms_deduper", None) or MemoryDeduper()
    outbox = getattr(runtime, "sms_outbox", None) or MemoryOutbox()
    runtime.sms_deduper = deduper
    runtime.sms_outbox = outbox
    if not deduper.claim(message_id):
        return 200, {"received": 0}
    try:
        _handle_sms(runtime, canonical_phone(sender), redact_token(text), message_id, outbox)
    except Exception:
        deduper.release(message_id)
        raise
    return 200, {"received": 1}


def _handle_sms(runtime, sender: str, text: str, message_id: str, outbox: MemoryOutbox) -> None:
    from app.chat_ports import ChatPorts

    business_id = runtime.settings.demo_business_id
    conversation_id = conversation_id_for_sender(business_id, "sms", sender)
    customer = None
    customer_id = None
    if hasattr(runtime.repository, "customer_by_phone"):
        found = runtime.repository.customer_by_phone(business_id, sender)
        if found is not None:
            customer_id = found.id
            customer = CustomerInput(full_name=found.full_name, phone=found.phone)
    store = getattr(runtime, "chat_store", None)
    if store is not None and hasattr(store, "create_session"):
        try:
            store.require_session(conversation_id)
        except DomainError:
            store.create_session(conversation_id, "")
    request = ChatRequest(session_id=conversation_id, business_id=business_id, message=text)
    ports = ChatPorts(runtime, request, None, customer, customer_id)
    catalog = ports.catalog(business_id)
    result = runtime.workflow.handle_turn(
        TurnContext(
            conversation_id=conversation_id,
            business_id=business_id,
            message=text,
            today=local_today(catalog).isoformat(),
            auth_kind="customer" if customer_id else "anonymous",
            customer_id=customer_id,
            customer_ready=customer is not None,
            input_modality="text",
        ),
        ports,
    )
    outbox.add(sender, result.text, message_id)
