import json
import traceback
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from app.channels.telegram import TelegramApiError, TelegramBotApi


def test_api_returns_decoded_result(monkeypatch):
    response = BytesIO(json.dumps({"ok": True, "result": {"id": 42}}).encode())
    monkeypatch.setattr(
        "app.channels.telegram.request.urlopen", lambda *a, **kw: response
    )
    assert TelegramBotApi("fake-token").get_me() == {"id": 42}


@pytest.mark.parametrize(
    "body, expected_code, retry_after",
    [
        (b'{"parameters":{"retry_after":17}}', 429, 17),
        (b"not json", 503, None),
        (b"x" * 1_000_001, 403, None),
    ],
    ids=["retry-metadata", "malformed-body", "oversized-body"],
)
def test_http_errors_keep_status_and_retry_without_leaking_token(
    monkeypatch, body, expected_code, retry_after
):
    error = HTTPError(
        "https://api.telegram.org/botfake-secret/sendMessage",
        expected_code,
        "fake-secret",
        {},
        BytesIO(body),
    )
    monkeypatch.setattr(
        "app.channels.telegram.request.urlopen", Mock(side_effect=error)
    )
    api = TelegramBotApi("fake-secret")
    with pytest.raises(TelegramApiError) as caught:
        api.send_message(101, "Hello")
    assert caught.value.code == f"telegram_http_{expected_code}"
    assert caught.value.retry_after == retry_after
    assert "fake-secret" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "body, expected_code",
    [
        (b"not json", "telegram_transport_error"),
        (b"x" * 1_000_001, "telegram_response_too_large"),
        (b"[]", "telegram_api_error"),
    ],
    ids=["malformed-json", "oversized-body", "invalid-envelope"],
)
def test_malformed_and_oversized_responses_are_bounded(
    monkeypatch, body, expected_code
):
    monkeypatch.setattr(
        "app.channels.telegram.request.urlopen", lambda *a, **kw: BytesIO(body)
    )
    with pytest.raises(TelegramApiError, match=f"^{expected_code}$"):
        TelegramBotApi("fake-token").get_me()


def test_telegram_rejection_exposes_retry_metadata(monkeypatch):
    body = b'{"ok":false,"error_code":429,"parameters":{"retry_after":12}}'
    monkeypatch.setattr(
        "app.channels.telegram.request.urlopen", lambda *a, **kw: BytesIO(body)
    )
    with pytest.raises(TelegramApiError) as caught:
        TelegramBotApi("fake-token").send_message(101, "Hello")
    assert caught.value.code == "telegram_api_429"
    assert caught.value.retry_after == 12
    assert not caught.value.is_permanent_rejection


def test_transport_failure_hides_credentials(monkeypatch):
    monkeypatch.setattr(
        "app.channels.telegram.request.urlopen",
        Mock(side_effect=URLError("https://example.com/botfake-secret")),
    )
    api = TelegramBotApi("fake-secret")
    with pytest.raises(TelegramApiError) as caught:
        api.get_me()
    assert caught.value.code == "telegram_transport_error"
    assert "fake-secret" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "metadata, expected_code",
    [
        ({}, "telegram_invalid_file"),
        ({"file_path": "", "file_size": 1}, "telegram_invalid_file"),
        ({"file_path": "voice/audio.ogg", "file_size": 11}, "telegram_file_too_large"),
    ],
)
def test_invalid_file_metadata_does_not_start_download(
    monkeypatch, metadata, expected_code
):
    api = TelegramBotApi("fake-token")
    monkeypatch.setattr(api, "call", lambda *a: metadata)
    download = Mock()
    monkeypatch.setattr(api, "download_file", download)
    with pytest.raises(TelegramApiError, match=f"^{expected_code}$"):
        api.get_file("recording", max_bytes=10)
    download.assert_not_called()
