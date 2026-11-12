import os
import time
from uuid import uuid4

import pytest
from redis import Redis

from app.graph.builder import build_graph
from app.graph.contracts import TurnContext
from app.graph.redis_checkpoint import RedisCheckpointSaver
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import DeterministicInterpreter
from tests.unit.test_conversation_graph import FakeSchedulingActions


def test_checkpoint_restart_expiry_and_isolation():
    url = os.getenv("TEST_REDIS_URL")
    if not url and os.getenv("CAPY_REQUIRE_INTEGRATION") == "1":
        pytest.fail("CI requires TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is required for Redis integration")
    client = Redis.from_url(url, decode_responses=True)
    thread = "test-" + uuid4().hex

    def workflow():
        return LangGraphWorkflow(
            build_graph(
                DeterministicInterpreter(), checkpointer=RedisCheckpointSaver(client, 1)
            )
        )

    turn = TurnContext(
        conversation_id=thread,
        business_id="demo-studio",
        message="Book Massage",
        today="2026-10-19",
        customer_id="customer-1",
        idempotency_key="redis-test",
    )
    try:
        workflow().handle_turn(turn, FakeSchedulingActions())
        assert workflow().draft(thread).service_id == "massage"
        assert workflow().messages("other-" + thread) == []
        time.sleep(1.1)
        assert workflow().messages(thread) == []
        assert workflow().draft(thread).service_id is None
    finally:
        keys = list(client.scan_iter(match=f"lg:v2:*:{thread}:*"))
        if keys:
            client.delete(*keys)
        client.close()
