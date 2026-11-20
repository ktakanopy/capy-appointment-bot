import logging
from base64 import b64encode
from unittest.mock import Mock

import pytest

from app.graph.builder import build_graph
from app.graph.decision import DeterministicDecision
from app.graph.redis_checkpoint import RedisCheckpointSaver
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import ProviderError
from app.llm.provider import DeterministicInterpreter
from tests.conftest import sample_slot
from tests.unit.test_conversation_graph import FakeSchedulingActions, _turn


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttl = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    def expire(self, key, seconds):
        if key not in self.values:
            return False
        self.ttl[key] = seconds
        return True

    def delete(self, *keys):
        return sum(1 for key in keys if self.values.pop(key, None) is not None)


def test_redis_checkpoint_keeps_the_draft_for_the_session_thread():
    redis = FakeRedis()
    workflow = LangGraphWorkflow(
        build_graph(
            DeterministicInterpreter(),
            DeterministicDecision(),
            checkpointer=RedisCheckpointSaver(redis, ttl_seconds=3600),
        )
    )
    actions = FakeSchedulingActions()

    workflow.handle_turn(_turn("Book Massage"), actions)
    result = workflow.handle_turn(_turn("tomorrow"), actions)

    assert result.intent == "search"
    assert workflow.draft("session-1").service_id == "massage"
    assert all(ttl == 3600 for ttl in redis.ttl.values())
    assert all("bk1." not in item["content"] for item in workflow.messages("session-1"))


def test_audio_forks_published_offer_and_checkpoints_only_redacted_text(caplog):
    saver = RedisCheckpointSaver(FakeRedis(), ttl_seconds=3600)
    interpreter = Mock(wraps=DeterministicInterpreter())
    router = Mock(wraps=DeterministicDecision())
    token = f"bk1.{'a' * 36}.123.{'b' * 64}"
    transcript = f"The second one. Ada Example +5511999998888 {token}"
    recording = b"private-audio-marker"
    transcribe = Mock(side_effect=[ProviderError("audio_processing_timeout"), transcript])
    workflow = LangGraphWorkflow(
        build_graph(interpreter, router, checkpointer=saver, transcribe=transcribe)
    )
    second_slot = sample_slot().model_copy(update={"slot_id": "slot-2"})
    actions = FakeSchedulingActions(slots=[sample_slot(), second_slot])
    offer = _turn("Book Massage on 2099-01-05", customer_id="customer-1")
    voice = _turn(None, customer_id="customer-1")
    workflow.handle_turn(offer, actions)
    previous_history = workflow.messages(offer.conversation_id)
    workflow.handle_turn(offer, actions)
    assert interpreter.interpret.call_args.args[1]["history"] == previous_history
    published_history = workflow.messages(offer.conversation_id)
    assert len(published_history) == 2 * len(previous_history)

    assert saver.fork_completed(offer.conversation_id, "failed")
    interpreter.reset_mock()
    router.reset_mock()
    with pytest.raises(ProviderError, match="audio_processing_timeout"):
        workflow.handle_turn(voice, actions, recording=recording, checkpoint_thread="failed")
    interpreter.interpret.assert_not_called()
    router.decide.assert_not_called()
    assert actions.created == []
    assert workflow.messages(offer.conversation_id) == published_history

    assert saver.fork_completed(offer.conversation_id, "success")
    with caplog.at_level(logging.DEBUG, logger="service_scheduling"):
        selection = workflow.handle_turn(
            voice,
            actions,
            recording=recording,
            display_name="Ada Example",
            checkpoint_thread="success",
        )
    assert transcribe.call_count == 2
    assert transcribe.call_args.args == (
        recording,
        {"service_names": ["Massage"], "professional_names": ["Alex"], "today": voice.today},
    )
    message, context = interpreter.interpret.call_args.args
    assert message == "The second one. [customer] [contact] [private-token]"
    assert context["history"] == published_history
    assert context["has_slots"]
    assert selection.intent == "request_booking_confirmation"
    assert actions.created == []
    workflow.handle_turn(
        _turn("Yes, book it.", customer_id="customer-1"),
        actions,
        checkpoint_thread="success",
    )
    assert actions.created == [("slot-2", "test-turn")]
    assert workflow.messages("success").count({"role": "user", "content": message}) == 1

    for thread in ("failed", "success"):
        checkpoints = list(saver.list({"configurable": {"thread_id": thread}}))
        assert any(item.pending_writes for item in checkpoints)
        persisted = repr(checkpoints)
        private_values = (
            recording.decode(),
            b64encode(recording).decode(),
            "Ada Example",
            "+5511999998888",
            token,
        )
        for private_value in private_values:
            assert private_value not in persisted
            assert private_value not in caplog.text
