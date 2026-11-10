import hashlib
import hmac
import json

import pytest

from app.channels.whatsapp import MemoryDeduper, process_inbound, verify_signature
from app.models import DomainError


def _signed(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_signature_rejects_tampered_body():
    body = b'{"ok":true}'
    assert verify_signature("secret", body, _signed("secret", body))
    assert not verify_signature("secret", body + b"x", _signed("secret", body))


def test_duplicate_webhook_does_not_run_the_workflow_twice():
    class Workflow:
        def __init__(self):
            self.calls = 0

        def handle_turn(self, turn, ports):
            _ = turn, ports
            self.calls += 1
            from app.graph.contracts import TurnResult

            return TurnResult(text="ok", intent="clarify")

    class Repository:
        def catalog(self, business_id):
            from tests.conftest import sample_catalog

            _ = business_id
            return sample_catalog()

    class Settings:
        whatsapp_app_secret = "secret"
        whatsapp_phone_number_id = "phone-1"
        demo_business_id = "demo-studio"

    class Runtime:
        def __init__(self):
            self.settings = Settings()
            self.repository = Repository()
            self.workflow = Workflow()
            self.logger = None
            self.tracer = None
            self.availability = None
            self.bookings = None
            self.whatsapp_deduper = MemoryDeduper()

    runtime = Runtime()
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "metadata": {"phone_number_id": "phone-1"},
                    "messages": [{
                        "from": "5511999999999",
                        "id": "wamid.1",
                        "type": "text",
                        "text": {"body": "when is my appointment"},
                    }],
                }
            }]
        }]
    }
    body = json.dumps(payload).encode()
    signature = _signed("secret", body)

    first = process_inbound(runtime, body, signature)
    second = process_inbound(runtime, body, signature)

    assert first[0] == second[0] == 200
    assert runtime.workflow.calls == 1
    assert len(runtime.whatsapp_outbox.messages) == 1


def test_missing_whatsapp_secret_is_not_configured():
    class Runtime:
        settings = type("Settings", (), {"whatsapp_app_secret": None})()

    with pytest.raises(DomainError) as caught:
        process_inbound(Runtime(), b"{}", "sha256=abc")

    assert caught.value.status_code == 503
