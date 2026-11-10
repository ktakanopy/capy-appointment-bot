import hashlib
import hmac
import json

import pytest

from app.channels.sms import process_sms
from app.models import DomainError


def _signed(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_sms_uses_the_same_workflow_and_dedupes():
    class Workflow:
        def __init__(self):
            self.calls = 0

        def handle_turn(self, turn, ports):
            _ = ports
            self.calls += 1
            self.modality = turn.input_modality
            from app.graph.contracts import TurnResult

            return TurnResult(text="ok", intent="clarify")

    class Repository:
        def catalog(self, business_id):
            from tests.conftest import sample_catalog

            _ = business_id
            return sample_catalog()

    class Settings:
        sms_webhook_secret = "sms-secret"
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

    runtime = Runtime()
    body = json.dumps({"message_id": "sms-1", "from": "5511999999999", "text": "Book Massage tomorrow"}).encode()
    signature = _signed("sms-secret", body)

    assert process_sms(runtime, body, signature)[0] == 200
    assert process_sms(runtime, body, signature)[1] == {"received": 0}
    assert runtime.workflow.calls == 1
    assert runtime.workflow.modality == "text"


def test_sms_redacts_a_pasted_management_token():
    seen = []

    class Workflow:
        def handle_turn(self, turn, ports):
            _ = ports
            seen.append(turn.message)
            from app.graph.contracts import TurnResult

            return TurnResult(text="ok", intent="help")

    class Repository:
        def catalog(self, business_id):
            from tests.conftest import sample_catalog

            _ = business_id
            return sample_catalog()

    class Settings:
        sms_webhook_secret = "sms-secret"
        demo_business_id = "demo-studio"

    runtime = type("Runtime", (), {})()
    runtime.settings = Settings()
    runtime.repository = Repository()
    runtime.workflow = Workflow()
    runtime.logger = None
    runtime.tracer = None
    runtime.availability = None
    runtime.bookings = None
    token = "bk1.123e4567-e89b-12d3-a456-426614174000.1." + "a" * 64
    body = json.dumps({"message_id": "sms-token", "from": "5511999999999", "text": f"help {token}"}).encode()

    process_sms(runtime, body, _signed("sms-secret", body))

    assert seen == ["help [redacted-token]"]
    assert token not in seen[0]


def test_sms_requires_configuration():
    class Runtime:
        settings = type("Settings", (), {"sms_webhook_secret": None})()

    with pytest.raises(DomainError) as caught:
        process_sms(Runtime(), b"{}", "abc")

    assert caught.value.status_code == 503
