import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.channels.bots import TelegramBot
from app.channels.telegram import TelegramApiError
from app.channels.telegram_intake import private_message
from app.graph.builder import build_graph
from app.graph.steps import load_context
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import ProviderError
from app.llm.provider import DeterministicInterpreter
from app.models import DomainError
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
    worker = TelegramWorker(runtime, None, repo, TelegramBot(42, "demo-studio", "TOKEN"), None)
    reply = reply_to_update(worker, text="cancel my booking")
    assert "No booking change" in reply
    assert actions.created == actions.canceled == actions.rescheduled == []


@pytest.fixture
def worker():
    def workflow(turn, actions, **kwargs):
        load_context({"business_id": turn.business_id}, actions)
        return SimpleNamespace(text="Done.")

    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            demo_business_id="demo-studio",
            telegram_audio_max_bytes=20000000,
        ),
        repository=SimpleNamespace(
            business=Mock(),
            catalog=Mock(return_value=sample_catalog()),
        ),
        workflow=SimpleNamespace(
            handle_turn=Mock(side_effect=workflow)
        ),
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
    return TelegramWorker(runtime, api, repository, TelegramBot(42, "demo-studio", "TOKEN"), "attempt")


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
    reply, _ = worker.handle_message(row, message, "previous")
    return reply


@pytest.mark.parametrize("text", ["/start", "/start@capy_bot", " /START "])
def test_start_returns_welcome_without_running_models(worker, text):
    reply = reply_to_update(worker, text=text)
    worker.runtime.workflow.handle_turn.assert_not_called()
    worker.runtime.repository.catalog.assert_not_called()
    assert "You can type" in reply


def test_blank_text_returns_supported_input_hint(worker):
    reply = reply_to_update(worker, text="   ")
    worker.runtime.workflow.handle_turn.assert_not_called()
    worker.runtime.repository.catalog.assert_not_called()
    assert "Send text or a voice message" in reply


def test_text_turn_passes_identity_idempotency_and_redaction_context(worker):
    reply = reply_to_update(worker, text="Ada wants Massage tomorrow +5511999998888")
    turn, actions = worker.runtime.workflow.handle_turn.call_args.args
    assert turn.customer_id == actions.customer_id == "customer-1"
    assert turn.idempotency_key == "telegram:42:10"
    assert worker.runtime.workflow.handle_turn.call_args.kwargs["display_name"] == "Ada"
    assert worker.runtime.workflow.handle_turn.call_args.kwargs["recording"] is None
    assert turn.input_modality == "text"
    with pytest.raises(DomainError, match="Business not found"):
        actions.catalog("another-business")
    worker.runtime.repository.business.assert_not_called()
    worker.runtime.repository.catalog.assert_called_once_with("demo-studio")
    assert reply == "Done."


@pytest.mark.parametrize("kind", ["voice", "audio"])
def test_telegram_downloads_audio_and_passes_it_to_one_workflow_turn(worker, kind):
    reply_to_update(worker, **{kind: {"file_id": "file-1"}})
    worker.api.get_file.assert_called_once_with("file-1", 20000000)
    turn = worker.runtime.workflow.handle_turn.call_args.args[0]
    assert turn.input_modality == "audio"
    assert turn.message is None
    assert worker.runtime.workflow.handle_turn.call_args.kwargs["recording"] == b"recording"
    worker.runtime.repository.business.assert_not_called()
    worker.runtime.repository.catalog.assert_called_once_with("demo-studio")
    worker.runtime.workflow.handle_turn.assert_called_once()


def test_telegram_rejects_over_limit_audio_before_starting_a_turn(worker):
    worker.api.get_file.side_effect = TelegramApiError("telegram_file_too_large")

    reply_to_update(worker, voice={"file_id": "file-1"})

    worker.runtime.workflow.handle_turn.assert_not_called()
    worker.runtime.repository.catalog.assert_not_called()


def test_each_turn_uses_the_current_business_timezone(worker, monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 18, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("app.telegram_worker.datetime", FixedDatetime)
    catalog = sample_catalog()
    worker.runtime.repository.catalog.side_effect = [
        catalog.model_copy(
            update={"business": catalog.business.model_copy(update={"timezone": timezone_name})}
        )
        for timezone_name in ("America/Sao_Paulo", "UTC")
    ]

    reply_to_update(worker, text="Book Massage tomorrow")
    reply_to_update(worker, text="Book Massage tomorrow")

    turns = [call.args[0] for call in worker.runtime.workflow.handle_turn.call_args_list]
    assert [turn.today for turn in turns] == ["2026-11-17", "2026-11-18"]
    assert worker.runtime.repository.catalog.call_count == 2


@pytest.mark.parametrize(
    "message", [{"text": "Book Massage tomorrow"}, {"voice": {"file_id": "file-1"}}]
)
def test_missing_business_stops_turn_and_returns_domain_message(worker, message):
    worker.runtime.repository.catalog.return_value = None

    reply = reply_to_update(worker, **message)

    worker.runtime.workflow.handle_turn.assert_not_called()
    assert reply == "The business is not available."


def test_audio_provider_failure_never_runs_partial_turn(worker):
    worker.runtime.workflow.handle_turn.side_effect = ProviderError(
        "failed_chunk"
    )
    row = claimed_update(voice={"file_id": "file-1"})
    reply, checkpoint = worker.handle_message(row, private_message(row), "previous")
    worker.runtime.workflow.handle_turn.assert_called_once()
    assert checkpoint == "previous"
    assert "No booking change" in reply


def test_checkpoint_is_promoted_only_after_a_completed_turn(worker):
    row = claimed_update(text="/start")
    _, checkpoint = worker.handle_message(row, private_message(row), "previous")
    assert checkpoint == "previous"
    row = claimed_update(text="Book Massage tomorrow")
    _, checkpoint = worker.handle_message(row, private_message(row), "previous")
    assert checkpoint == "attempt"


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
    worker.runtime.repository.catalog.assert_not_called()


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
