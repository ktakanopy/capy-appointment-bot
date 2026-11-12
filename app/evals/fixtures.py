"""Synthetic service boundary for task evaluations; PostgreSQL is tested separately."""
from datetime import datetime, timedelta, timezone
from app.graph.contracts import BookingRef
from app.models import Business, CatalogResponse, Service, Professional, AvailabilitySlot, Booking, BookingSummary, Customer


class ScenarioPorts:
    def __init__(self, context, existing_booking=False):
        self.context = context
        self.last_booking = None
        self.bookings = {}
        self.writes = {"create": 0, "confirm": 0, "cancel": 0, "reschedule": 0}
        if existing_booking:
            self.bookings["fixture-booking"] = self._booking("fixture-booking", self.slot())

    def catalog(self, business_id):
        return CatalogResponse(
            business=Business(id=business_id, name="Synthetic studio", category="wellness", timezone="UTC"),
            services=[Service(id="massage", business_id=business_id, name=self.context.get("service_names", ["Massage"])[0],
                              description="A one hour massage", duration_minutes=60, price_minor=10000)],
            professionals=[Professional(id="alex", business_id=business_id, name="Alex")],
        )

    def slot(self, start=None):
        start = start or datetime(2099, 1, 5, 15, tzinfo=timezone.utc)
        return AvailabilitySlot(slot_id=f"fixture-{start.isoformat()}", business_id="demo-studio", service_id="massage",
                                professional_id="alex", professional_name="Alex", starts_at=start,
                                ends_at=start + timedelta(hours=1), local_label=start.isoformat(), duration_minutes=60,
                                price_minor=10000, currency="BRL")

    def search(self, request):
        start = datetime.combine(request.start_date, datetime.min.time(), timezone.utc).replace(hour=15)
        return [self.slot(start), self.slot(start + timedelta(hours=2))]

    def _booking(self, booking_id, slot):
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        return Booking(id=booking_id, business_id="demo-studio", service_id="massage", professional_id="alex",
                       customer=Customer(id="fixture-customer", business_id="demo-studio", full_name="Synthetic customer"),
                       starts_at=slot.starts_at, ends_at=slot.ends_at, status="scheduled", created_at=now, updated_at=now)

    def create_booking(self, slot, key):
        self.writes["create"] += 1
        booking = self._booking(f"fixture-{len(self.bookings)+1}", slot)
        self.bookings[booking.id] = self.last_booking = booking
        return BookingSummary(booking=booking)

    def confirm(self, booking_id, key):
        return self._change(booking_id, "confirm", {"status": "confirmed"})

    def cancel(self, booking_id, key):
        return self._change(booking_id, "cancel", {"status": "canceled"})

    def reschedule(self, booking_id, slot, key):
        return self._change(booking_id, "reschedule", {"starts_at": slot.starts_at, "ends_at": slot.ends_at})

    def _change(self, booking_id, operation, updates):
        self.writes[operation] += 1
        self.last_booking = self.bookings[booking_id].model_copy(update=updates)
        self.bookings[booking_id] = self.last_booking
        return self.last_booking, {"confirm": "confirmed", "cancel": "canceled", "reschedule": "rescheduled"}[operation]

    def list_booking_refs(self):
        return [BookingRef(id=b.id, service_id=b.service_id, professional_id=b.professional_id, label=b.starts_at.isoformat())
                for b in self.bookings.values() if b.status != "canceled"]

    def record_escalation(self, conversation_id):
        pass
