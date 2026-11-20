import logging
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from langgraph.checkpoint.memory import InMemorySaver

from app.channels.telegram_repository import TelegramRepository
from app.customer_scheduling_actions import CustomerSchedulingActions
from app.graph.builder import build_graph
from app.graph.contracts import TurnContext
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import DeterministicInterpreter
from tests.integration.helpers import connect, next_open_day


def test_real_workflow_booking_reschedule_cancel(scheduling_db, caplog):
    runtime = SimpleNamespace(
        repository=scheduling_db["repository"],
        availability=scheduling_db["availability"],
        bookings=scheduling_db["bookings"],
        logger=logging.getLogger("journey"),
        tracer=None,
    )
    identity = TelegramRepository(scheduling_db["url"]).ensure_customer_for_sender(
        "demo-studio", 42, 101, "Synthetic customer"
    )
    workflow = LangGraphWorkflow(
        build_graph(DeterministicInterpreter(), checkpointer=InMemorySaver())
    )
    catalog = runtime.repository.catalog("demo-studio")
    sequence = []

    def turn(message):
        key = f"journey-{len(sequence)}"
        actions = CustomerSchedulingActions(
            runtime, "demo-studio", identity["customer_id"], "journey"
        )
        result = workflow.handle_turn(
            TurnContext(
                conversation_id="journey",
                business_id="demo-studio",
                message=message,
                today=next_open_day().isoformat(),
                customer_id=identity["customer_id"],
                idempotency_key=key,
            ),
            actions,
        )
        sequence.append(result)
        return result

    with caplog.at_level(logging.INFO, logger="journey"):
        slots = turn(
            f"Book {catalog.services[0].name} on {next_open_day().isoformat()}"
        )
        assert slots.intent == "search" and slots.slots
        selection = turn("1")
        assert selection.intent == "request_booking_confirmation"
        assert selection.booking is None
        with connect(scheduling_db["url"]) as conn:
            assert conn.execute(
                "SELECT count(*) AS total FROM bookings WHERE customer_id=%s",
                (identity["customer_id"],),
            ).fetchone()["total"] == 0
        created = turn("Yes, book it.")
        assert created.intent == "create"
        assert catalog.services[0].name in created.text and "Ana" in created.text
        local_start = created.booking.starts_at.astimezone(
            ZoneInfo(catalog.business.timezone)
        )
        assert local_start.strftime("%H:%M") in created.text
        assert catalog.business.timezone in created.text
        assert created.booking.starts_at == slots.slots[0].starts_at
        with connect(scheduling_db["url"]) as conn:
            assert conn.execute(
                "SELECT count(*) AS total FROM bookings WHERE customer_id=%s",
                (identity["customer_id"],),
            ).fetchone()["total"] == 1
        assert turn("confirm my booking").booking.status.value == "confirmed"
        moved_slots = turn(f"move my appointment to {next_open_day().isoformat()}")
        assert moved_slots.intent == "reschedule" and moved_slots.slots
        moved = turn("2")
        assert moved.intent == "reschedule" and moved.booking.id == created.booking.id
        assert moved.booking.starts_at != created.booking.starts_at
        assert turn("cancel my booking").booking.status.value == "canceled"
        assert turn("when is my appointment").intent == "lookup"
    assert "Synthetic customer" not in str(workflow.state("journey"))
    assert "Synthetic customer" not in caplog.text
    assert "booking.operation" in caplog.text
