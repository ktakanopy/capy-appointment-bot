from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import AvailabilitySearchRequest, BookingCreateRequest, CustomerInput


def next_open_day() -> date:
    day = datetime.now(ZoneInfo("America/Sao_Paulo")).date() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


def offered_slots(runtime, target_date: date | None = None):
    target_date = target_date or next_open_day()
    return runtime["availability"].search(AvailabilitySearchRequest(
        business_id="demo-studio",
        service_id="service-demo",
        start_date=target_date,
        end_date=target_date,
    ))


def slot_at_offset(slots, *, professional_id: str, after: datetime, minutes: int):
    target = after.astimezone(timezone.utc) + timedelta(minutes=minutes)
    return next(
        slot for slot in slots
        if slot.professional_id == professional_id and slot.starts_at == target
    )


def create_booking(runtime, slot, *, key: str, phone: str):
    request = BookingCreateRequest(
        business_id="demo-studio",
        service_id=slot.service_id,
        professional_id=slot.professional_id,
        slot_id=slot.slot_id,
        starts_at=slot.starts_at,
        customer=CustomerInput(full_name="Integration Customer", phone=phone),
    )
    return runtime["bookings"].create(request, key)
