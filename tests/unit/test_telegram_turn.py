import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.channels.telegram_intake import private_message
from app.graph.builder import build_graph
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import ProviderError
from app.llm.provider import DeterministicInterpreter
from app.telegram_worker import TelegramWorker
from tests.conftest import sample_catalog
from tests.unit.test_conversation_graph import FakeSchedulingActions


def test_failed_laya_produces_retry_and_no_booking_write(monkeypatch):
    class BrokenLaya:
        def decide(self, request, draft):
            raise ProviderError("unavailable")

    actions = FakeSchedulingActions()
    monkeypatch.setattr(
        "app.telegram_worker.CustomerSchedulingActions", lambda *args, **kwargs: actions
    )
    runtime = SimpleNamespace(
        settings=SimpleNamespace(demo_business_id="demo-studio"),
        logger=logging.getLogger("test"),
        repository=SimpleNamespace(business=lambda _: sample_catalog().business),
        workflow=LangGraphWorkflow(
            build_graph(DeterministicInterpreter(), BrokenLaya())
        ),
    )
    repo = SimpleNamespace(
        ensure_customer_for_sender=lambda *args: {
            "customer_id": "customer-1",
            "telegram_user_id": 101,
        },
    )
    worker = TelegramWorker(runtime, None, repo)
    worker.bot_id = 42
    reply = reply_to_update(worker, text="cancel my booking")
    assert "No booking change" in reply
    assert actions.created == actions.canceled == actions.rescheduled == []


@pytest.fixture
def worker():
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            demo_business_id="demo-studio",
            telegram_audio_max_bytes=20000000,
            telegram_audio_processing_timeout_seconds=900,
        ),
        repository=SimpleNamespace(
            business=Mock(return_value=sample_catalog().business),
            catalog=Mock(return_value=sample_catalog()),
        ),
        workflow=SimpleNamespace(
            handle_turn=Mock(return_value=SimpleNamespace(text="Done."))
        ),
        audio_understanding=SimpleNamespace(
            transcribe_recording=Mock(return_value="Book Massage tomorrow")
        ),
        audio_chunker=object(),
        logger=logging.getLogger("telegram-refactor"),
    )
    repository = Mock()
    repository.ensure_customer_for_sender.return_value = {
        "telegram_user_id": 101,
        "customer_id": "customer-1",
        "display_name": "Ada",
    }
    api = Mock()
    api.get_file.return_value = b"recording"
    worker = TelegramWorker(runtime, api, repository)
    worker.bot_id = 42
    return worker


def claimed_update(**content):
    return dict(
        update_id=10,
        chat_id=101,
        telegram_user_id=101,
        payload={
            "message": {
                "chat": {"type": "private", "id": 101},
                "from": {"id": 101, "username": "ada"},
                **content,
            }
        },
    )


def reply_to_update(worker, **content):
    row = claimed_update(**content)
    message = private_message(row)
    assert message is not None
    identity = worker._ensure_customer(message["from"], row["telegram_user_id"])
    return worker._reply_to_message(row, identity, message)


@pytest.mark.parametrize("text", ["/start", "/start@capy_bot", " /START "])
def test_start_returns_welcome_without_running_models(worker, text):
    reply = reply_to_update(worker, text=text)
    worker.runtime.workflow.handle_turn.assert_not_called()
    assert "You can type" in reply


def test_blank_text_returns_supported_input_hint(worker):
    reply = reply_to_update(worker, text="   ")
    worker.runtime.workflow.handle_turn.assert_not_called()
    assert "Send text or a voice message" in reply


def test_text_turn_keeps_identity_idempotency_and_privacy(worker):
    reply = reply_to_update(worker, text="Ada wants Massage tomorrow +5511999998888")
    turn, actions = worker.runtime.workflow.handle_turn.call_args.args
    assert turn.customer_id == actions.customer_id == "customer-1"
    assert turn.idempotency_key == "telegram:42:10"
    assert "Ada" not in turn.message and "+5511999998888" not in turn.message
    assert turn.input_modality == "text"
    worker.runtime.repository.business.assert_called_once_with("demo-studio")
    worker.runtime.repository.catalog.assert_not_called()
    assert reply == "Done."


@pytest.mark.parametrize("kind", ["voice", "audio"])
def test_audio_finishes_transcription_before_one_workflow_turn(worker, kind):
    events = []

    def transcribe(audio, context, **kwargs):
        catalog = worker.runtime.repository.catalog.return_value
        assert context["service_names"] == [item.name for item in catalog.services]
        assert context["professional_names"] == [
            item.name for item in catalog.professionals
        ]
        events.append("transcription-complete")
        return "Book Massage tomorrow"

    def workflow(*args):
        events.append("workflow")
        return SimpleNamespace(text="Done.")

    worker.runtime.audio_understanding.transcribe_recording.side_effect = transcribe
    worker.runtime.workflow.handle_turn.side_effect = workflow
    reply_to_update(worker, **{kind: {"file_id": "file-1"}})
    assert events == ["transcription-complete", "workflow"]
    worker.api.get_file.assert_called_once_with("file-1", 20000000)
    turn = worker.runtime.workflow.handle_turn.call_args.args[0]
    assert turn.input_modality == "audio"
    worker.runtime.repository.catalog.assert_called_once_with("demo-studio")
    worker.runtime.workflow.handle_turn.assert_called_once()


def test_turn_resolves_date_in_the_business_timezone(worker, monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 18, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("app.telegram_worker.datetime", FixedDatetime)
    worker.runtime.repository.business.return_value = SimpleNamespace(
        timezone="America/Sao_Paulo"
    )

    reply_to_update(worker, text="Book Massage tomorrow")

    turn = worker.runtime.workflow.handle_turn.call_args.args[0]
    assert turn.today == "2026-11-17"


def test_missing_business_stops_turn_and_returns_domain_message(worker):
    worker.runtime.repository.business.return_value = None

    reply = reply_to_update(worker, text="Book Massage tomorrow")

    worker.runtime.workflow.handle_turn.assert_not_called()
    assert reply == "The business is not available."


def test_audio_provider_failure_never_runs_partial_turn(worker):
    worker.runtime.audio_understanding.transcribe_recording.side_effect = ProviderError(
        "failed_chunk"
    )
    reply = reply_to_update(worker, voice={"file_id": "file-1"})
    worker.runtime.workflow.handle_turn.assert_not_called()
    assert "No booking change" in reply


@pytest.mark.parametrize("contact_id, saves", [(101, True), (999, False)])
def test_contact_only_updates_own_profile_without_running_workflow(
    worker, contact_id, saves
):
    reply_to_update(worker, contact={"user_id": contact_id, "phone_number": "+5511999998888"})
    assert worker.repository.save_contact.called == saves
    if saves:
        worker.repository.save_contact.assert_called_once_with(
            "demo-studio", "customer-1", "+5511999998888"
        )
    worker.runtime.workflow.handle_turn.assert_not_called()


@pytest.mark.parametrize(
    "chat,sender",
    [
        ({"type": "group"}, {"id": 101}),
        ({"type": "private"}, {"id": 999}),
        ([], {"id": 101}),
        ({"type": "private"}, []),
    ],
)
def test_invalid_channel_or_sender_is_ignored(chat, sender):
    row = claimed_update(text="book")
    row["payload"]["message"].update(chat=chat, **{"from": sender})
    assert private_message(row) is None
