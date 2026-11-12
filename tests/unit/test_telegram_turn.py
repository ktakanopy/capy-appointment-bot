import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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
        repository=SimpleNamespace(catalog=lambda _: sample_catalog()),
        workflow=LangGraphWorkflow(
            build_graph(DeterministicInterpreter(), BrokenLaya())
        ),
    )
    replies = []
    repo = SimpleNamespace(
        ensure_customer_for_sender=lambda *args: {
            "customer_id": "customer-1",
            "telegram_user_id": 101,
        },
        save_reply=lambda *args: replies.append(args[2]),
    )
    worker = TelegramWorker(runtime, None, repo)
    worker.bot_id = 42
    worker._process_one(
        dict(
            update_id=10,
            chat_id=101,
            telegram_user_id=101,
            payload={
                "message": {
                    "chat": {"type": "private"},
                    "from": {"id": 101},
                    "text": "cancel my booking",
                }
            },
        )
    )
    assert "No booking change" in replies[0]
    assert actions.created == actions.canceled == actions.rescheduled == []


# These exercise the worker boundary without Telegram, model, or database calls.
from app.channels.telegram import TelegramApiError


@pytest.fixture
def worker():
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            demo_business_id="demo-studio",
            telegram_poll_seconds=20,
            telegram_audio_max_bytes=20000000,
            telegram_audio_processing_timeout_seconds=900,
        ),
        repository=SimpleNamespace(catalog=lambda _: sample_catalog()),
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


@pytest.mark.parametrize("text", ["/start", "/start@capy_bot", " /START "])
def test_start_saves_welcome_without_running_models(worker, text):
    worker._process_one(claimed_update(text=text))
    worker.runtime.workflow.handle_turn.assert_not_called()
    worker.repository.save_reply.assert_called_once()
    assert "You can type" in worker.repository.save_reply.call_args.args[2]


def test_blank_text_returns_supported_input_hint(worker):
    worker._process_one(claimed_update(text="   "))
    worker.runtime.workflow.handle_turn.assert_not_called()
    assert (
        "Send text or a voice message" in worker.repository.save_reply.call_args.args[2]
    )


def test_text_turn_keeps_identity_idempotency_and_privacy(worker):
    worker._process_one(
        claimed_update(text="Ada wants Massage tomorrow +5511999998888")
    )
    turn, actions = worker.runtime.workflow.handle_turn.call_args.args
    assert turn.customer_id == actions.customer_id == "customer-1"
    assert turn.idempotency_key == "telegram:42:10"
    assert "Ada" not in turn.message and "+5511999998888" not in turn.message
    assert turn.input_modality == "text"
    worker.repository.save_reply.assert_called_once_with(42, 10, "Done.")


@pytest.mark.parametrize("kind", ["voice", "audio"])
def test_audio_finishes_transcription_before_one_workflow_turn(worker, kind):
    events = []

    def transcribe(*args, **kwargs):
        events.append("transcription-complete")
        return "Book Massage tomorrow"

    def workflow(*args):
        events.append("workflow")
        return SimpleNamespace(text="Done.")

    worker.runtime.audio_understanding.transcribe_recording.side_effect = transcribe
    worker.runtime.workflow.handle_turn.side_effect = workflow
    worker._process_one(claimed_update(**{kind: {"file_id": "file-1"}}))
    assert events == ["transcription-complete", "workflow"]
    worker.api.get_file.assert_called_once_with("file-1", 20000000)
    turn = worker.runtime.workflow.handle_turn.call_args.args[0]
    assert turn.input_modality == "audio"
    worker.runtime.workflow.handle_turn.assert_called_once()


def test_audio_provider_failure_never_runs_partial_turn(worker):
    worker.runtime.audio_understanding.transcribe_recording.side_effect = ProviderError(
        "failed_chunk"
    )
    worker._process_one(claimed_update(voice={"file_id": "file-1"}))
    worker.runtime.workflow.handle_turn.assert_not_called()
    assert "No booking change" in worker.repository.save_reply.call_args.args[2]


@pytest.mark.parametrize("contact_id, saves", [(101, True), (999, False)])
def test_contact_only_updates_own_profile_without_running_workflow(
    worker, contact_id, saves
):
    worker._process_one(
        claimed_update(
            contact={"user_id": contact_id, "phone_number": "+5511999998888"}
        )
    )
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
def test_invalid_channel_or_sender_is_ignored_before_identity_lookup(
    worker, chat, sender
):
    row = claimed_update(text="book")
    row["payload"]["message"].update(chat=chat, **{"from": sender})
    worker._process_one(row)
    worker.repository.mark_ignored.assert_called_once_with(42, 10)
    worker.repository.ensure_customer_for_sender.assert_not_called()
    worker.runtime.workflow.handle_turn.assert_not_called()


def test_reply_rate_limit_preserves_order_and_does_not_rerun_workflow(worker):
    worker.repository.ready_replies.return_value = [
        dict(
            update_id=i, chat_id=101, reply_text="Saved", reply_markup=None, attempts=0
        )
        for i in (10, 11)
    ]
    worker.api.send_message.side_effect = TelegramApiError(
        "telegram_api_429", retry_after=8
    )
    worker._deliver_ready_replies()
    worker.api.send_message.assert_called_once()
    worker.repository.delay_reply.assert_called_once_with(42, 10, 8, "telegram_api_429")
    worker.repository.mark_delivered.assert_not_called()
    worker.runtime.workflow.handle_turn.assert_not_called()


def test_permanently_rejected_reply_does_not_block_the_next_reply(worker):
    worker.repository.ready_replies.return_value = [
        dict(
            update_id=i, chat_id=101, reply_text="Saved", reply_markup=None, attempts=0
        )
        for i in (10, 11)
    ]
    worker.api.send_message.side_effect = [TelegramApiError("telegram_api_403"), None]
    worker._deliver_ready_replies()
    worker.repository.discard_reply.assert_called_once_with(42, 10, "telegram_api_403")
    worker.repository.mark_delivered.assert_called_once_with(42, 11)


def test_polling_saves_updates_before_advancing_offset(worker):
    worker.api.get_updates.return_value = [
        {"update_id": "bad"},
        {
            "update_id": 10,
            "message": {"chat": {"id": 101}, "from": {"id": 101}, "text": "hello"},
        },
    ]
    assert worker._poll_updates(5) == 11
    worker.repository.save_update.assert_called_once()
    assert worker.repository.save_update.call_args.kwargs["update_id"] == 10


def test_poll_failure_keeps_offset_and_waits_for_retry(worker, monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("app.telegram_worker.time.sleep", sleep)
    worker.api.get_updates.side_effect = TelegramApiError(
        "telegram_api_429", retry_after=4
    )
    assert worker._poll_updates(5) == 5
    worker.repository.save_update.assert_not_called()
    sleep.assert_called_once_with(4)
