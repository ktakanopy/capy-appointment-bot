import json

import httpx

from frontend.lib.api_client import BackendClient


def test_chat_client_keeps_token_out_of_message_and_uses_idempotency_header():
    captured = {}

    def handler(request):
        captured["payload"] = json.loads(request.content)
        captured["headers"] = request.headers
        return httpx.Response(200, json={"session_id": "s1", "intent": "confirm"})

    client = BackendClient("http://api.test", client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test"))
    token = "bk1.123e4567-e89b-12d3-a456-426614174000.1." + "b" * 64

    result = client.send_message("s1", "confirm this", manage_token=token, idempotency_key="key-1")

    assert result["intent"] == "confirm"
    assert captured["payload"]["message"] == "confirm this"
    assert captured["payload"]["manage_token"] == token
    assert token not in captured["payload"]["message"]
    assert captured["headers"]["Idempotency-Key"] == "key-1"


def test_manage_api_client_sends_capability_in_header():
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, json={"outcome": "canceled"})

    client = BackendClient("http://api.test", client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test"))
    token = "private-capability"

    client.manage("cancel", token, idempotency_key="cancel-1")

    assert captured["request"].headers["X-Booking-Token"] == token
    assert captured["request"].content == b""
