from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.graph.contracts import WorkflowDraft
from app.models import AvailabilitySlot, DomainError


_TOKEN_PATTERN = re.compile(r"\bbk1\.[0-9a-fA-F-]{36}\.\d+\.[0-9a-f]{64}\b")
_MAX_HISTORY_MESSAGES = 24


class RedisSessionValueMap:
    """Expose selected Redis session fields through the mapping methods chat uses."""

    def __init__(
        self,
        redis_client: Any,
        *,
        key_for_session: Callable[[str], str],
        field: str,
        encode: Callable[[Any], str],
        decode: Callable[[str], Any],
        ttl_seconds: int,
    ):
        self._redis = redis_client
        self._key_for_session = key_for_session
        self._field = field
        self._encode = encode
        self._decode = decode
        self._ttl_seconds = ttl_seconds

    def __getitem__(self, session_id: str):
        raw_value = self._redis.hget(self._key_for_session(session_id), self._field)
        if raw_value is None:
            raise KeyError(session_id)
        return self._decode(raw_value)

    def __setitem__(self, session_id: str, value: Any) -> None:
        key = self._key_for_session(session_id)
        if not self._redis.exists(key):
            raise KeyError(session_id)
        self._redis.hset(key, self._field, self._encode(value))
        self._redis.expire(key, self._ttl_seconds)

    def get(self, session_id: str, default=None):
        try:
            return self[session_id]
        except KeyError:
            return default

    def pop(self, session_id: str, default=None):
        key = self._key_for_session(session_id)
        raw_value = self._redis.hget(key, self._field)
        if raw_value is None:
            return default
        self._redis.hdel(key, self._field)
        self._redis.expire(key, self._ttl_seconds)
        return self._decode(raw_value)


class RedisChatStore:
    """Store chat sessions, offered slots, and redacted conversation turns in Redis."""

    def __init__(self, redis_url: str, ttl_seconds: int):
        from redis import Redis

        self._redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        self._ttl_seconds = ttl_seconds
        self.session_slots = RedisSessionValueMap(
            self._redis,
            key_for_session=self._state_key,
            field="slots",
            encode=self._encode_slots,
            decode=self._decode_slots,
            ttl_seconds=ttl_seconds,
        )
        self.pending_slots = RedisSessionValueMap(
            self._redis,
            key_for_session=self._state_key,
            field="pending_slot",
            encode=self._encode_slot,
            decode=self._decode_slot,
            ttl_seconds=ttl_seconds,
        )

    @staticmethod
    def _state_key(session_id: str) -> str:
        return f"chat:session:{session_id}:state"

    @staticmethod
    def _messages_key(session_id: str) -> str:
        return f"chat:session:{session_id}:messages"

    @staticmethod
    def _encode_slots(slots: list[AvailabilitySlot]) -> str:
        return json.dumps(
            [slot.model_dump(mode="json") for slot in slots],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode_slots(raw_value: str) -> list[AvailabilitySlot]:
        return [AvailabilitySlot.model_validate(item) for item in json.loads(raw_value)]

    @staticmethod
    def _encode_slot(slot: AvailabilitySlot) -> str:
        return slot.model_dump_json()

    @staticmethod
    def _decode_slot(raw_value: str) -> AvailabilitySlot:
        return AvailabilitySlot.model_validate_json(raw_value)

    @staticmethod
    def _message(role: str, content: str) -> str:
        safe_content = _TOKEN_PATTERN.sub("[redacted-token]", content)
        return json.dumps(
            {"role": role, "content": safe_content},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def ping(self) -> bool:
        return bool(self._redis.ping())

    def close(self) -> None:
        self._redis.close()

    def create_session(self, session_id: str, welcome_message: str) -> None:
        state_key = self._state_key(session_id)
        messages_key = self._messages_key(session_id)
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.delete(state_key, messages_key)
        pipeline.hset(state_key, mapping={"active": "1", "slots": "[]"})
        pipeline.expire(state_key, self._ttl_seconds)
        if welcome_message:
            pipeline.rpush(messages_key, self._message("assistant", welcome_message))
            pipeline.expire(messages_key, self._ttl_seconds)
        pipeline.execute()

    def require_session(self, session_id: str) -> None:
        state_key = self._state_key(session_id)
        if not self._redis.exists(state_key):
            raise DomainError("session_not_found", "Session not found. Start a new session.", 404)
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.expire(state_key, self._ttl_seconds)
        pipeline.expire(self._messages_key(session_id), self._ttl_seconds)
        pipeline.execute()

    def load_draft(self, session_id: str) -> WorkflowDraft:
        raw_value = self._redis.hget(self._state_key(session_id), "draft")
        if not raw_value:
            return WorkflowDraft()
        return WorkflowDraft.model_validate_json(raw_value)

    def save_draft(self, session_id: str, draft: WorkflowDraft) -> None:
        key = self._state_key(session_id)
        if not self._redis.exists(key):
            raise DomainError("session_not_found", "Session not found. Start a new session.", 404)
        self._redis.hset(key, "draft", draft.model_dump_json())
        self._redis.expire(key, self._ttl_seconds)

    def load_slots(self, session_id: str) -> list[AvailabilitySlot]:
        return self.session_slots.get(session_id, [])

    def save_slots(self, session_id: str, slots: list[AvailabilitySlot]) -> None:
        self.session_slots[session_id] = slots

    def get_messages(self, session_id: str) -> list[dict[str, str]]:
        raw_messages = self._redis.lrange(
            self._messages_key(session_id), -_MAX_HISTORY_MESSAGES, -1
        )
        messages: list[dict[str, str]] = []
        for raw_message in raw_messages:
            try:
                message = json.loads(raw_message)
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                isinstance(message, dict)
                and message.get("role") in {"user", "assistant"}
                and isinstance(message.get("content"), str)
            ):
                messages.append(message)
        return messages

    def append_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        state_key = self._state_key(session_id)
        if not self._redis.exists(state_key):
            raise DomainError("session_not_found", "Session not found. Start a new session.", 404)
        messages_key = self._messages_key(session_id)
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.rpush(
            messages_key,
            self._message("user", user_message),
            self._message("assistant", assistant_message),
        )
        pipeline.ltrim(messages_key, -_MAX_HISTORY_MESSAGES, -1)
        pipeline.expire(messages_key, self._ttl_seconds)
        pipeline.expire(state_key, self._ttl_seconds)
        pipeline.execute()
