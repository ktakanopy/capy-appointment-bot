from __future__ import annotations

import json
from urllib import parse, request
from urllib.error import HTTPError, URLError


class TelegramApiError(RuntimeError):
    def __init__(self, code: str, *, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after

    @property
    def is_permanent_rejection(self) -> bool:
        return self.code in {
            "telegram_api_400",
            "telegram_api_403",
            "telegram_http_400",
            "telegram_http_403",
        }


_MAX_RESPONSE_BYTES = 1_000_000


class TelegramBotApi:
    """Small Telegram Bot API client that never includes the token in errors."""

    def __init__(self, token: str, api_base_url: str = "https://api.telegram.org"):
        self._token = token
        self._api_base_url = api_base_url.rstrip("/")

    def call(self, method: str, payload: dict | None = None, *, timeout: float = 30):
        url = f"{self._api_base_url}/bot{self._token}/{method}"
        request_body = json.dumps(payload or {}).encode()
        api_request = request.Request(
            url, data=request_body, headers={"content-type": "application/json"}
        )
        try:
            with request.urlopen(api_request, timeout=timeout) as response:
                result = _decode_response(response)
        except HTTPError as error:
            raise _http_failure(error) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise TelegramApiError("telegram_transport_error") from None

        if not isinstance(result, dict):
            raise TelegramApiError("telegram_api_error")
        if not result.get("ok"):
            error_code = result.get("error_code")
            code = (
                f"telegram_api_{error_code}"
                if isinstance(error_code, int)
                else "telegram_api_error"
            )
            raise TelegramApiError(code, retry_after=_retry_after(result))
        return result.get("result")

    def get_me(self) -> dict:
        result = self.call("getMe")
        if not isinstance(result, dict) or not isinstance(result.get("id"), int):
            raise TelegramApiError("telegram_invalid_bot_identity")
        return result

    def has_webhook(self) -> bool:
        result = self.call("getWebhookInfo")
        return bool(isinstance(result, dict) and result.get("url"))

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self.call(
            "getUpdates",
            payload,
            timeout=timeout + 10,
        )
        if not isinstance(result, list):
            raise TelegramApiError("telegram_invalid_updates")
        return [item for item in result if isinstance(item, dict)]

    def download_file(self, file_path: str, max_bytes: int) -> bytes:
        # Telegram supplies file_path. Restrict it to a relative path so it
        # cannot change the host or escape the bot API file endpoint.
        if (
            not file_path
            or file_path.startswith(("/", "\\"))
            or ".." in file_path.split("/")
            or "\\" in file_path
        ):
            raise TelegramApiError("telegram_invalid_file_path")
        relative_path = parse.quote(file_path, safe="/._-")
        url = f"{self._api_base_url}/file/bot{self._token}/{relative_path}"
        try:
            with request.urlopen(url, timeout=45) as response:
                data = response.read(max_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise TelegramApiError("telegram_file_download_failed") from None
        if len(data) > max_bytes:
            raise TelegramApiError("telegram_file_too_large")
        return data

    def get_file(self, file_id: str, max_bytes: int) -> bytes:
        result = self.call("getFile", {"file_id": file_id})
        if not isinstance(result, dict):
            raise TelegramApiError("telegram_invalid_file")
        size = result.get("file_size")
        path = result.get("file_path")
        if not isinstance(path, str) or not path:
            raise TelegramApiError("telegram_invalid_file")
        if isinstance(size, int) and size > max_bytes:
            raise TelegramApiError("telegram_file_too_large")
        return self.download_file(path, max_bytes)

    def send_message(
        self, chat_id: int, text: str, reply_markup: dict | None = None
    ) -> None:
        payload: dict = {"chat_id": chat_id, "text": text[:4000]}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.call("sendMessage", payload)


def _decode_response(response):
    body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise TelegramApiError("telegram_response_too_large")
    return json.loads(body.decode())


def _http_failure(error: HTTPError) -> TelegramApiError:
    """Read retry metadata without exposing the credential-bearing request URL."""
    retry_after = None
    try:
        retry_after = _retry_after(_decode_response(error))
    except (TelegramApiError, ValueError, OSError):
        pass
    return TelegramApiError(f"telegram_http_{error.code}", retry_after=retry_after)


def _retry_after(result: object) -> int | None:
    if not isinstance(result, dict):
        return None
    parameters = result.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("retry_after")
    return value if isinstance(value, int) and value > 0 else None
