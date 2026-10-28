from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import AvailabilitySearchRequest, BookingCreateRequest, CustomerInput


def test_customer_input_normalizes_name_phone_and_email():
    customer = CustomerInput(
        full_name="  Ada   Lovelace ",
        phone="+55 (11) 99999-9999",
        email="ADA@example.com",
    )

    assert customer.full_name == "Ada Lovelace"
    assert customer.phone == "+5511999999999"
    assert customer.email == "ada@example.com"


@pytest.mark.parametrize("phone", ["123", "+55+11999999999", "abc1234567"])
def test_customer_input_rejects_invalid_phone(phone):
    with pytest.raises(ValidationError):
        CustomerInput(full_name="Ada Lovelace", phone=phone)


def test_booking_requires_timezone_aware_start():
    with pytest.raises(ValidationError, match="starts_at must include a UTC offset"):
        BookingCreateRequest(
            service_id="massage",
            professional_id="alex",
            slot_id="slot-1",
            starts_at=datetime(2099, 1, 5, 15),
            customer={"full_name": "Ada Lovelace", "phone": "+5511999999999"},
        )


def test_booking_normalizes_start_to_utc():
    request = BookingCreateRequest(
        service_id="massage",
        professional_id="alex",
        slot_id="slot-1",
        starts_at=datetime.fromisoformat("2099-01-05T12:00:00-03:00"),
        customer={"full_name": "Ada Lovelace", "phone": "+5511999999999"},
    )

    assert request.starts_at == datetime(2099, 1, 5, 15, tzinfo=timezone.utc)


def test_availability_range_is_bounded():
    with pytest.raises(ValidationError, match="availability range cannot exceed 60 days"):
        AvailabilitySearchRequest(
            service_id="massage",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 3),
        )


def test_contracts_reject_unknown_fields():
    with pytest.raises(ValidationError):
        AvailabilitySearchRequest(
            service_id="massage",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
            internal_sql="DROP TABLE bookings",
        )
