from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.main import app, get_health_dependencies


def test_retired_customer_and_owner_surfaces_are_absent():
    client = TestClient(app)
    for method, path in [
        ("POST", "/chat"),
        ("POST", "/sessions/new"),
        ("POST", "/bookings"),
        ("GET", "/bookings/me"),
        ("GET", "/owner/bookings"),
        ("PATCH", "/owner/settings"),
    ]:
        assert client.request(method, path, json={}).status_code == 404


def test_health_reports_storage_failure_as_unready(monkeypatch):
    monkeypatch.setattr("app.main.database_is_ready", lambda _: False)
    app.dependency_overrides[get_health_dependencies] = lambda: SimpleNamespace(
        settings=SimpleNamespace(database_url="unused"),
        redis=SimpleNamespace(ping=lambda: True),
    )
    try:
        response = TestClient(app).get("/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
    finally:
        app.dependency_overrides.clear()


def test_health_startup_uses_storage_without_constructing_ai_runtime(monkeypatch):
    settings = SimpleNamespace(chat_redis_url="redis://unused", database_url="unused")
    redis = Mock()
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.Redis.from_url", Mock(return_value=redis))
    monkeypatch.setattr("app.main.database_is_ready", lambda _: True)
    runtime_factory = Mock(side_effect=AssertionError("AI runtime is not needed"))
    monkeypatch.setattr("app.runtime.create_runtime", runtime_factory)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        redis.ping.assert_called_once()
        runtime_factory.assert_not_called()
    redis.close.assert_called_once()
