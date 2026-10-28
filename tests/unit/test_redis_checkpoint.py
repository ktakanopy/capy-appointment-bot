from app.graph.builder import build_graph
from app.graph.decision import DeterministicDecision
from app.graph.redis_checkpoint import RedisCheckpointSaver
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import DeterministicInterpreter
from tests.unit.test_conversation_graph import Ports, _turn


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
    ports = Ports()

    workflow.handle_turn(_turn("Book Massage"), ports)
    result = workflow.handle_turn(_turn("tomorrow"), ports)

    assert result.intent == "search"
    assert workflow.draft("session-1").service_id == "massage"
    assert all(ttl == 3600 for ttl in redis.ttl.values())
    assert all("bk1." not in item["content"] for item in workflow.messages("session-1"))
