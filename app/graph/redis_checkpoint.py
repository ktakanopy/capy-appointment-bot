from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Sequence
from typing import Any

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)


def _encode(typed: tuple[str, bytes]) -> list[str]:
    type_name, payload = typed
    return [type_name, base64.b64encode(payload).decode("ascii")]


def _decode(value: list[str]) -> tuple[str, bytes]:
    return value[0], base64.b64decode(value[1])


class RedisCheckpointSaver(BaseCheckpointSaver[str]):
    """LangGraph checkpoint saver for the Redis already used by chat sessions.

    Official RedisSaver needs RedisJSON and RediSearch. This saver stores the
    same checkpoint contract in plain Redis keys and expires them with the
    session TTL. It is thread state, not appointment truth.
    """

    def __init__(self, redis_client, ttl_seconds: int):
        super().__init__()
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds

    def get_tuple(self, config) -> CheckpointTuple | None:
        thread_id, checkpoint_ns = self._thread(config)
        checkpoint_id = get_checkpoint_id(config) or self._latest_id(thread_id, checkpoint_ns)
        if not checkpoint_id:
            return None
        item = self._read_json(self._item_key(thread_id, checkpoint_ns, checkpoint_id))
        if item is None:
            return None
        self._touch(thread_id, checkpoint_ns, checkpoint_id)
        checkpoint = self.serde.loads_typed(_decode(item["checkpoint"]))
        metadata = self.serde.loads_typed(_decode(item["metadata"]))
        parent_id = item.get("parent")
        writes = self._read_json(self._writes_key(thread_id, checkpoint_ns, checkpoint_id)) or []
        return CheckpointTuple(
            config=self._config(thread_id, checkpoint_ns, checkpoint_id),
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=self._config(thread_id, checkpoint_ns, parent_id) if parent_id else None,
            pending_writes=[
                (row["task_id"], row["channel"], self.serde.loads_typed(_decode(row["value"])))
                for row in writes
            ],
        )

    def list(self, config, *, filter=None, before=None, limit=None) -> Iterator[CheckpointTuple]:
        _ = filter, before, limit
        if config is None:
            return
        found = self.get_tuple({"configurable": {"thread_id": config["configurable"]["thread_id"], "checkpoint_ns": config["configurable"].get("checkpoint_ns", "")}})
        if found is not None:
            yield found

    def put(self, config, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions):
        _ = new_versions
        thread_id, checkpoint_ns = self._thread(config)
        checkpoint_id = checkpoint["id"]
        payload = {
            "checkpoint": _encode(self.serde.dumps_typed(checkpoint)),
            "metadata": _encode(self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))),
            "parent": config["configurable"].get("checkpoint_id"),
        }
        self._write_json(self._item_key(thread_id, checkpoint_ns, checkpoint_id), payload)
        self.redis.set(self._latest_key(thread_id, checkpoint_ns), checkpoint_id, ex=self.ttl_seconds)
        return self._config(thread_id, checkpoint_ns, checkpoint_id)

    def put_writes(self, config, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        thread_id, checkpoint_ns = self._thread(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]
        key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id)
        existing = self._read_json(key) or []
        seen = {(row["task_id"], row["idx"]) for row in existing}
        for idx, (channel, value) in enumerate(writes):
            write_idx = WRITES_IDX_MAP.get(channel, idx)
            identity = (task_id, write_idx)
            if write_idx >= 0 and identity in seen:
                continue
            existing.append(
                {
                    "task_id": task_id,
                    "idx": write_idx,
                    "channel": channel,
                    "value": _encode(self.serde.dumps_typed(value)),
                    "task_path": task_path,
                }
            )
        self._write_json(key, existing)

    def delete_thread(self, thread_id: str) -> None:
        latest = self._latest_key(thread_id, "")
        checkpoint_id = self.redis.get(latest)
        keys = [latest]
        if checkpoint_id:
            keys.append(self._item_key(thread_id, "", checkpoint_id))
            keys.append(self._writes_key(thread_id, "", checkpoint_id))
        self.redis.delete(*keys)

    def _thread(self, config) -> tuple[str, str]:
        configurable = config["configurable"]
        return configurable["thread_id"], configurable.get("checkpoint_ns", "")

    def _latest_id(self, thread_id: str, checkpoint_ns: str) -> str | None:
        value = self.redis.get(self._latest_key(thread_id, checkpoint_ns))
        return value if isinstance(value, str) else None

    def _touch(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> None:
        self.redis.expire(self._latest_key(thread_id, checkpoint_ns), self.ttl_seconds)
        self.redis.expire(self._item_key(thread_id, checkpoint_ns, checkpoint_id), self.ttl_seconds)
        self.redis.expire(self._writes_key(thread_id, checkpoint_ns, checkpoint_id), self.ttl_seconds)

    def _read_json(self, key: str):
        raw = self.redis.get(key)
        if not raw:
            return None
        return json.loads(raw)

    def _write_json(self, key: str, payload) -> None:
        self.redis.set(key, json.dumps(payload), ex=self.ttl_seconds)

    @staticmethod
    def _latest_key(thread_id: str, checkpoint_ns: str) -> str:
        return f"lg:ckpt:{thread_id}:{checkpoint_ns}:latest"

    @staticmethod
    def _item_key(thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        return f"lg:ckpt:{thread_id}:{checkpoint_ns}:{checkpoint_id}"

    @staticmethod
    def _writes_key(thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        return f"lg:writes:{thread_id}:{checkpoint_ns}:{checkpoint_id}"

    @staticmethod
    def _config(thread_id: str, checkpoint_ns: str, checkpoint_id: str):
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }
