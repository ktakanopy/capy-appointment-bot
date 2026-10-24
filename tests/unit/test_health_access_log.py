import logging

from app.main import _SuppressHealthAccess


def test_health_access_log_is_suppressed():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:49402", "GET", "/health", "1.1", 200),
        exc_info=None,
    )

    assert _SuppressHealthAccess().filter(record) is False


def test_other_access_logs_are_kept():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:49402", "POST", "/chat", "1.1", 200),
        exc_info=None,
    )

    assert _SuppressHealthAccess().filter(record) is True
