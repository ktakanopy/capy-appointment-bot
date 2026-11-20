"""Synthetic service boundary for task evaluations; PostgreSQL is tested separately."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.graph.contracts import BookingRef
from app.models import (
    AvailabilitySlot,
    Booking,
    BookingSummary,
    Business,
    CatalogResponse,
    Customer,
    Professional,
    Service,
)


class ScenarioSchedulingActions:
    """Emulate scheduling effects for isolated conversations without database writes."""

    def __init__(self, context, existing_booking=False):
        """Create fresh booking, write-count, and idempotency state for one attempt."""
        self.context = context
        self.last_booking = None
        self.bookings = {}
        self.writes = {"create": 0, "confirm": 0, "cancel": 0, "reschedule": 0}
        self.idempotency = {}
        if existing_booking:
            self.bookings["fixture-booking"] = self._booking(
                "fixture-booking", self.slot()
            )

    def catalog(self, business_id):
        """Return the stable synthetic business catalog used by the graph."""
        return CatalogResponse(
            business=Business(
                id=business_id,
                name="Synthetic studio",
                category="wellness",
                timezone=self.context.get("business_timezone", "UTC"),
            ),
            services=[
                Service(
                    id="massage",
                    business_id=business_id,
                    name=self.context.get("service_names", ["Massage"])[0],
                    description="A one hour massage",
                    duration_minutes=60,
                    price_minor=10000,
                )
            ],
            professionals=[
                Professional(id="alex", business_id=business_id, name="Alex")
            ],
        )

    def slot(self, start=None):
        """Build a slot from a UTC instant and format its label in business time."""
        zone = ZoneInfo(self.context.get("business_timezone", "UTC"))
        start = start or datetime(2099, 1, 5, 15, tzinfo=zone).astimezone(timezone.utc)
        local_start = start.astimezone(zone)
        return AvailabilitySlot(
            slot_id=f"fixture-{start.isoformat()}",
            business_id="demo-studio",
            service_id="massage",
            professional_id="alex",
            professional_name="Alex",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            local_label=f"{local_start:%Y-%m-%d %H:%M} {zone.key}",
            duration_minutes=60,
            price_minor=10000,
            currency="BRL",
        )

    def search(self, request):
        """Offer two repeatable times on the requested date for slot-selection cases."""
        zone = ZoneInfo(self.context.get("business_timezone", "UTC"))
        start = datetime.combine(request.start_date, time(15), zone).astimezone(timezone.utc)
        return [self.slot(start), self.slot(start + timedelta(hours=2))]

    def _booking(self, booking_id, slot):
        """Build deterministic booking records without relying on the current clock."""
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        return Booking(
            id=booking_id,
            business_id="demo-studio",
            service_id="massage",
            professional_id="alex",
            customer=Customer(
                id="fixture-customer",
                business_id="demo-studio",
                full_name="Synthetic customer",
            ),
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            status="scheduled",
            created_at=now,
            updated_at=now,
        )

    def create_booking(self, slot, key):
        """Count a synthetic create once and reuse its result for a repeated key."""
        if key in self.idempotency:
            self.last_booking = self.idempotency[key]
            return BookingSummary(booking=self.last_booking)
        self.writes["create"] += 1
        booking = self._booking(f"fixture-{len(self.bookings) + 1}", slot)
        self.bookings[booking.id] = self.last_booking = booking
        self.idempotency[key] = booking
        return BookingSummary(booking=booking)

    def confirm(self, booking_id, key):
        """Apply an existing-booking confirmation and expose its write count."""
        return self._change(booking_id, "confirm", {"status": "confirmed"}, key)

    def cancel(self, booking_id, key):
        """Apply an existing-booking cancellation and expose its write count."""
        return self._change(booking_id, "cancel", {"status": "canceled"}, key)

    def reschedule(self, booking_id, slot, key):
        """Move an existing synthetic booking while keeping a separate write count."""
        return self._change(
            booking_id,
            "reschedule",
            {"starts_at": slot.starts_at, "ends_at": slot.ends_at},
            key,
        )

    def _change(self, booking_id, operation, updates, key):
        """Apply one idempotent status or time change to the isolated fixture."""
        idempotency_key = (operation, key)
        if idempotency_key in self.idempotency:
            self.last_booking = self.idempotency[idempotency_key]
            return self.last_booking, {
                "confirm": "confirmed",
                "cancel": "canceled",
                "reschedule": "rescheduled",
            }[operation]
        self.writes[operation] += 1
        self.last_booking = Booking.model_validate(
            {**self.bookings[booking_id].model_dump(), **updates}
        )
        self.bookings[booking_id] = self.last_booking
        self.idempotency[idempotency_key] = self.last_booking
        return self.last_booking, {
            "confirm": "confirmed",
            "cancel": "canceled",
            "reschedule": "rescheduled",
        }[operation]

    def list_booking_refs(self):
        """Expose uncanceled bookings as references the workflow can resolve."""
        return [
            BookingRef(
                id=b.id,
                service_id=b.service_id,
                professional_id=b.professional_id,
                label=b.starts_at.isoformat(),
            )
            for b in self.bookings.values()
            if b.status != "canceled"
        ]

    def record_escalation(self, conversation_id):
        """Accept escalation without external side effects in an offline evaluation."""
        pass
