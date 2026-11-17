from unittest.mock import Mock

import pytest

from app.graph.contracts import WorkflowDraft
from app.local_models import RoutingTurn, route


@pytest.mark.parametrize(
    ("message", "cancellation_probability"),
    [
        ("Quero remarcar meu agendamento.", 0.08),
        ("Quero cancelar meu agendamento.", 0.91),
    ],
)
def test_local_cancel_confidence_requires_explicit_cancellation(
    message, cancellation_probability
):
    laya = Mock()
    laya.predict.return_value = {
        "answers": {
            "next_action": {
                "choice": "CANCEL",
                "probabilities": {"CANCEL": 0.87},
            },
            "explicit_cancellation": {"noul": cancellation_probability},
        }
    }
    turn = RoutingTurn(message=message, context=WorkflowDraft())

    decision = route(laya, turn)

    assert decision.next_action == "CANCEL"
    assert decision.confidence == min(0.87, cancellation_probability)
    questions = laya.predict.call_args.args[1]
    assert "BOOK" not in questions["next_action"]["criteria"]


def test_local_router_can_book_an_offered_time():
    laya = Mock()
    laya.predict.return_value = {
        "answers": {
            "next_action": {"choice": "BOOK", "probabilities": {"BOOK": 0.85}},
            "explicit_cancellation": {"noul": 0.01},
        }
    }
    turn = RoutingTurn(
        message="I choose option 1.",
        context=WorkflowDraft(operation="create", offered_slot_ids=["slot-1"]),
    )

    decision = route(laya, turn)

    assert decision.next_action == "BOOK"
    assert decision.confidence == 0.85
