from app.privacy import sanitize_message
from tests.unit.test_conversation_graph import FakeSchedulingActions, _turn, _workflow


def test_contacts_redacted_before_every_checkpoint_and_model_input():
    workflow, actions = _workflow(), FakeSchedulingActions()
    workflow.handle_turn(
        _turn("Book Massage 2099-01-05; +5511999998888; ada@example.com"), actions
    )
    snapshot = workflow.state("session-1")
    assert "2099-01-05" in snapshot["message"]
    assert "+5511999998888" not in str(snapshot)
    assert "ada@example.com" not in str(snapshot)
    for state in workflow.graph.get_state_history(
        {"configurable": {"thread_id": "session-1"}}
    ):
        assert "+5511999998888" not in str(state.values)
    assert (
        sanitize_message("Ada Lovelace says hello", "Ada Lovelace")
        == "[customer] says hello"
    )


def test_history_is_bounded_and_users_are_isolated():
    workflow, actions = _workflow(), FakeSchedulingActions()
    for _ in range(18):
        workflow.handle_turn(_turn("help"), actions)
    assert len(workflow.messages("session-1")) == 24
    assert workflow.messages("other-user") == []
