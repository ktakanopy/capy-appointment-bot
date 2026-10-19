from __future__ import annotations

import logging
import time as clock
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models import (
    AvailabilitySlot,
    Business,
    CatalogResponse,
    Professional,
    Service,
)


@pytest.fixture(autouse=True)
def deterministic_test_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deterministic")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setenv("TRACING_ENABLED", "false")


class FakeWorkflow:
    def __init__(self):
        from app.llm.schemas import SchedulingExtraction

        self.extraction = SchedulingExtraction(intent="unknown")
        self.messages: list[str] = []

    def interpret(self, session_id, message, **_context):
        self.messages.append(message)
        return self.extraction


class FakeAvailability:
    def __init__(self, slots=None):
        self.slots = list(slots or [])
        self.requests = []

    def search(self, request, **_kwargs):
        self.requests.append(request)
        return self.slots


class FakeBookings:
    def __init__(self):
        self.created = []
        self.supplied_tokens = []

    def create(self, request, idempotency_key):
        from app.models import BookingSummary

        self.created.append((request, idempotency_key))
        return BookingSummary(booking=sample_booking(request.customer), manage_token="demo-token")

    def read(self, token):
        self.supplied_tokens.append(token)
        return sample_booking()

    def cancel(self, token, _idempotency_key):
        self.supplied_tokens.append(token)
        return sample_booking(), "canceled"


def sample_catalog() -> CatalogResponse:
    return CatalogResponse(
        business=Business(
            id="demo-studio",
            name="Demo Studio",
            category="wellness",
            timezone="UTC",
            minimum_notice_minutes=5,
            booking_horizon_days=60,
        ),
        services=[Service(
            id="massage",
            business_id="demo-studio",
            name="Massage",
            description="A one hour massage.",
            duration_minutes=60,
            price_minor=10000,
        )],
        professionals=[Professional(id="alex", business_id="demo-studio", name="Alex")],
    )


def sample_slot(start: datetime | None = None) -> AvailabilitySlot:
    start = start or datetime(2099, 1, 5, 15, tzinfo=timezone.utc)
    return AvailabilitySlot(
        slot_id="slot-1",
        business_id="demo-studio",
        service_id="massage",
        professional_id="alex",
        professional_name="Alex",
        starts_at=start,
        ends_at=start.replace(hour=start.hour + 1),
        local_label="Mon 05 Jan, 15:00 UTC (UTC+0000)",
        duration_minutes=60,
        price_minor=10000,
        currency="BRL",
    )


def sample_booking(customer=None):
    from app.models import Booking, BookingStatus, Customer

    customer = customer or Customer(id="customer-1", business_id="demo-studio", full_name="Ada Lovelace", phone="+5511999999999")
    start = datetime(2099, 1, 5, 15, tzinfo=timezone.utc)
    return Booking(
        id="booking-1",
        business_id="demo-studio",
        service_id="massage",
        professional_id="alex",
        customer=Customer(id="customer-1", business_id="demo-studio", full_name=customer.full_name, phone=customer.phone, email=customer.email),
        starts_at=start,
        ends_at=start.replace(hour=16),
        status=BookingStatus.SCHEDULED,
        created_at=datetime(2098, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2098, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def api_runtime():
    catalog = sample_catalog()
    workflow = FakeWorkflow()
    availability = FakeAvailability()
    bookings = FakeBookings()

    class Repository:
        def catalog(self, business_id):
            return catalog if business_id == catalog.business.id else None

    class Runtime:
        def __init__(self):
            self.settings = SimpleNamespace(demo_business_id="demo-studio", session_ttl_minutes=60)
            self.logger = logging.getLogger("test-scheduling")
            self.tracer = None
            self.repository = Repository()
            self.availability = availability
            self.bookings = bookings
            self.workflow = workflow
            self.sessions = {"session-1": clock.time()}
            self.session_slots = {}
            self.pending_slots = {}

        def require_session(self, session_id):
            from app.models import DomainError

            if session_id not in self.sessions:
                raise DomainError("session_not_found", "Session not found.", 404)
            self.sessions[session_id] = clock.time()

    runtime = Runtime()
    runtime.catalog_fixture = catalog
    return runtime


@pytest.fixture
def client(api_runtime):
    from fastapi.testclient import TestClient

    from app.main import app, get_runtime

    app.dependency_overrides[get_runtime] = lambda: api_runtime
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
