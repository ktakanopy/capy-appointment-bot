from fastapi.testclient import TestClient
from app.main import app


def test_retired_customer_and_owner_surfaces_are_absent():
    client = TestClient(app)
    for method, path in [("POST", "/chat"), ("POST", "/sessions/new"), ("POST", "/bookings"),
                         ("GET", "/bookings/me"), ("GET", "/owner/bookings"), ("PATCH", "/owner/settings")]:
        assert client.request(method, path, json={}).status_code == 404


def test_health_reports_storage_failure_as_unready(monkeypatch):
    from types import SimpleNamespace
    from app.main import get_runtime
    monkeypatch.setattr("app.main.database_is_ready", lambda _: False)
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(settings=SimpleNamespace(database_url="unused"),redis=SimpleNamespace(ping=lambda: True))
    try:
        response = TestClient(app).get("/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
    finally:
        app.dependency_overrides.clear()
