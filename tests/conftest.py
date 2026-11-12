from datetime import datetime, timezone
from app.models import AvailabilitySlot, Business, CatalogResponse, Professional, Service

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
