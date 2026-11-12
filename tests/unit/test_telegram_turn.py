import logging
from types import SimpleNamespace
from app.telegram_worker import TelegramWorker
from app.graph.builder import build_graph
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import ProviderError
from app.llm.provider import DeterministicInterpreter
from tests.conftest import sample_catalog
from tests.unit.test_conversation_graph import Ports


def test_failed_laya_produces_retry_and_no_booking_write(monkeypatch):
    class BrokenLaya:
        def decide(self, request, draft):
            raise ProviderError("unavailable")
    ports = Ports()
    monkeypatch.setattr("app.telegram_worker.ChatPorts", lambda *args: ports)
    runtime = SimpleNamespace(settings=SimpleNamespace(demo_business_id="demo-studio"), logger=logging.getLogger("test"),
        repository=SimpleNamespace(catalog=lambda _: sample_catalog()),
        workflow=LangGraphWorkflow(build_graph(DeterministicInterpreter(), BrokenLaya())))
    replies = []
    repo = SimpleNamespace(ensure_identity=lambda *args: {"customer_id":"customer-1", "telegram_user_id":101},
                           save_reply=lambda *args: replies.append(args[2]))
    worker = TelegramWorker(runtime, None, repo)
    worker.bot_id = 42
    worker._process_one(dict(update_id=10,chat_id=101,telegram_user_id=101,payload={"message":{
        "chat":{"type":"private"},"from":{"id":101},"text":"cancel my booking"}}))
    assert "No booking change" in replies[0]
    assert ports.created == ports.canceled == ports.rescheduled == []
