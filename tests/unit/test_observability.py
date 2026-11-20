import logging

from app.observability import debug_event, debug_turn, get_logger, log_event, redact


def test_sensitive_fields_and_capability_tokens_are_redacted(caplog):
    token = "bk1.123e4567-e89b-12d3-a456-426614174000.1." + "a" * 64
    logger = logging.getLogger("test-redaction")

    with caplog.at_level(logging.INFO, logger="test-redaction"):
        log_event(
            logger,
            "booking",
            customer_name="Ada Lovelace",
            phone="+5511999999999",
            note=token,
        )

    assert "Ada Lovelace" not in caplog.text
    assert "+5511999999999" not in caplog.text
    assert token not in caplog.text
    assert "[redacted-token]" in caplog.text
    assert redact({"email": "ada@example.com", "nested": {"token": "secret"}}) == {
        "email": "[redacted]",
        "nested": {"token": "[redacted]"},
    }


def test_debug_events_require_debug_level_and_scope_update_ids(caplog):
    logger = get_logger()
    with caplog.at_level(logging.INFO, logger=logger.name):
        debug_event("workflow.hidden")
    assert not caplog.records

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        with debug_turn(42, 9):
            debug_event("workflow.example", phone="+5511999999999")
        debug_event("workflow.outside_turn")

    assert [record.msg for record in caplog.records] == [
        {
            "level": "debug",
            "event": "workflow.example",
            "phone": "[redacted]",
            "bot_id": 42,
            "update_id": 9,
        },
        {"level": "debug", "event": "workflow.outside_turn"},
    ]
