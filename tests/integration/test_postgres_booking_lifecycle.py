from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import update

import app.booking as booking_module
from app.db_models import ServiceRow
from app.models import (
    AvailabilitySearchRequest,
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingSummary,
    DomainError,
)
from app.seed import seed
from tests.integration.helpers import (
    connect,
    create_booking,
    offered_slots,
    slot_at_offset,
    test_customer,
    next_open_day,
)


def test_migration_and_seed_are_repeatable(scheduling_db):
    seed()
    seed()
    catalog = scheduling_db["repository"].catalog("demo-studio")

    assert catalog is not None
    assert len(catalog.professionals) == 1
    assert {service.name for service in catalog.services} == {
        "Shiatsu 30 min", "Shiatsu 60 min", "Liberação miofascial 60 min",
    }
    with connect(scheduling_db["url"]) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM businesses").fetchone()["n"]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM weekly_availability_rules"
            ).fetchone()["n"]
            == 6
        )


def test_service_options_are_data_and_durations_are_unique(scheduling_db):
    with connect(scheduling_db["url"]) as connection:
        connection.execute(
            "INSERT INTO services (id,business_id,service_type,duration_minutes,price_minor) "
            "VALUES ('shiatsu-45','demo-studio','Shiatsu',45,12000)"
        )
        connection.execute("UPDATE services SET price_minor=11000 WHERE id='service-shiatsu-30'")
    seed()
    catalog = scheduling_db["repository"].catalog("demo-studio")
    assert any(item.name == "Shiatsu 45 min" and item.duration_minutes == 45 for item in catalog.services)
    assert next(item.price_minor for item in catalog.services if item.id == "service-shiatsu-30") == 11000
    with pytest.raises(psycopg.errors.UniqueViolation):
        with connect(scheduling_db["url"]) as connection:
            connection.execute(
                "INSERT INTO services (id,business_id,service_type,duration_minutes,price_minor) "
                "VALUES ('duplicate','demo-studio','shiatsu',45,12000)"
            )


def test_duration_fits_hours_and_services_share_one_calendar(scheduling_db):
    day = next_open_day()
    with connect(scheduling_db["url"]) as connection:
        connection.execute(
            "UPDATE weekly_availability_rules SET closes_at='12:00' WHERE weekday=%s",
            (day.weekday(),),
        )

    def search(service_id):
        return scheduling_db["availability"].search(AvailabilitySearchRequest(
            business_id="demo-studio", service_id=service_id, start_date=day, end_date=day,
        ))

    short_slots = search("service-shiatsu-30")
    long_slots = search("service-demo")
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("America/Sao_Paulo")
    assert any(slot.starts_at.astimezone(zone).time() == time(11, 15) for slot in short_slots)
    assert all(slot.starts_at.astimezone(zone).time() != time(11, 15) for slot in long_slots)
    short_slot = short_slots[0]
    long_slot = next(slot for slot in long_slots if slot.starts_at == short_slot.starts_at)
    created = create_booking(scheduling_db, short_slot, key="short-massage", phone="+5511999900010")
    with pytest.raises(DomainError) as conflict:
        create_booking(scheduling_db, long_slot, key="overlap-massage", phone="+5511999900011")
    assert conflict.value.code == "slot_unavailable"
    with connect(scheduling_db["url"]) as connection:
        connection.execute("UPDATE services SET duration_minutes=45 WHERE id='service-shiatsu-30'")
        assert connection.execute("SELECT to_regclass('reminders') AS name").fetchone()["name"] is None
    saved = scheduling_db["bookings"].read(
        created.booking.business_id, created.booking.customer.id, created.booking.id,
    )
    assert saved.ends_at - saved.starts_at == timedelta(minutes=30)


def test_booking_writes_preserve_the_validated_buffer_interval(scheduling_db, monkeypatch):
    with connect(scheduling_db["url"]) as connection:
        connection.execute(
            "UPDATE services SET buffer_before_minutes=15, buffer_after_minutes=20 "
            "WHERE id='service-demo'"
        )
    validate = booking_module.validate_slot_in_transaction
    changes = iter(((30, 45), (0, 0)))

    def change_buffers_after_validation(session, **kwargs):
        slot = validate(session, **kwargs)
        before, after = next(changes)
        session.execute(update(ServiceRow).where(ServiceRow.id == "service-demo").values(
            buffer_before_minutes=before, buffer_after_minutes=after
        ))
        return slot

    monkeypatch.setattr(booking_module, "validate_slot_in_transaction", change_buffers_after_validation)
    first_slot = offered_slots(scheduling_db)[0]
    created = create_booking(scheduling_db, first_slot, key="buffer-create", phone="+5511999910010")
    with connect(scheduling_db["url"]) as connection:
        saved = connection.execute(
            "SELECT occupied_from, occupied_until FROM bookings WHERE id=%s", (created.booking.id,)
        ).fetchone()
    assert saved["occupied_from"] == first_slot.starts_at - timedelta(minutes=15)
    assert saved["occupied_until"] == first_slot.ends_at + timedelta(minutes=20)

    next_slot = offered_slots(scheduling_db)[-1]
    request = BookingRescheduleRequest(
        business_id=next_slot.business_id, service_id=next_slot.service_id,
        professional_id=next_slot.professional_id, slot_id=next_slot.slot_id,
        starts_at=next_slot.starts_at,
    )
    scheduling_db["bookings"].reschedule(
        created.booking.business_id, created.booking.customer.id, created.booking.id,
        request, "buffer-move",
    )
    with connect(scheduling_db["url"]) as connection:
        saved = connection.execute(
            "SELECT occupied_from, occupied_until FROM bookings WHERE id=%s", (created.booking.id,)
        ).fetchone()
    assert saved["occupied_from"] == next_slot.starts_at - timedelta(minutes=30)
    assert saved["occupied_until"] == next_slot.ends_at + timedelta(minutes=45)


def test_create_idempotency_and_postgres_overlap_constraint(scheduling_db):
    slots = offered_slots(scheduling_db)
    slot = slots[0]
    request = BookingCreateRequest(
        business_id=slot.business_id,
        service_id=slot.service_id,
        professional_id=slot.professional_id,
        slot_id=slot.slot_id,
        starts_at=slot.starts_at,
        customer_id=test_customer(scheduling_db, "+5511999910001"),
    )
    service = scheduling_db["bookings"]
    created = service.create(request, "create-replay-key")
    replay = service.create(request, "create-replay-key")

    assert replay.outcome == "replayed"
    assert replay.booking.id == created.booking.id
    changed = request.model_copy(
        update={"customer_id": test_customer(scheduling_db, "+5511999910002")}
    )
    with pytest.raises(DomainError) as reused_key:
        service.create(changed, "create-replay-key")
    assert reused_key.value.code == "idempotency_key_reused"

    with pytest.raises(DomainError) as occupied:
        service.create(request, "different-create-key")
    assert occupied.value.code == "slot_unavailable"

    adjacent_slot = slot_at_offset(
        slots,
        professional_id=slot.professional_id,
        after=slot.starts_at,
        minutes=75,
    )
    adjacent = create_booking(
        scheduling_db,
        adjacent_slot,
        key="adjacent-booking",
        phone="+5511999910003",
    )
    assert adjacent.booking.starts_at == adjacent_slot.starts_at

    overlapping_start = slot.starts_at + timedelta(minutes=15)
    overlapping_end = overlapping_start + timedelta(minutes=60)
    for status in ("scheduled", "canceled"):
        customer_id = str(uuid4())
        booking_id = str(uuid4())
        try:
            with connect(scheduling_db["url"]) as connection:
                connection.execute(
                    "INSERT INTO customers (id,business_id,full_name,phone) VALUES (%s,'demo-studio',%s,%s)",
                    (customer_id, f"Direct {status}", f"+55118888{booking_id[:4]}"),
                )
                connection.execute(
                    """INSERT INTO bookings (id,business_id,service_id,professional_id,customer_id,
                       starts_at,ends_at,occupied_from,occupied_until,status)
                       VALUES (%s,'demo-studio','service-demo',%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        booking_id,
                        slot.professional_id,
                        customer_id,
                        overlapping_start,
                        overlapping_end,
                        overlapping_start,
                        overlapping_end + timedelta(minutes=15),
                        status,
                    ),
                )
        except psycopg.errors.ExclusionViolation:
            if status != "scheduled":
                raise
        else:
            assert status == "canceled"


def test_failed_reschedule_preserves_booking_then_success_moves_it(
    scheduling_db,
):
    slots = offered_slots(scheduling_db)
    first_slot = slots[0]
    second_slot = slot_at_offset(
        slots,
        professional_id=first_slot.professional_id,
        after=first_slot.starts_at,
        minutes=75,
    )
    free_slot = slot_at_offset(
        slots,
        professional_id=first_slot.professional_id,
        after=first_slot.starts_at,
        minutes=150,
    )
    first = create_booking(
        scheduling_db, first_slot, key="reschedule-first", phone="+5511999920001"
    )
    second = create_booking(
        scheduling_db, second_slot, key="reschedule-second", phone="+5511999920002"
    )

    occupied_request = BookingRescheduleRequest(
        business_id=second_slot.business_id,
        service_id=second_slot.service_id,
        professional_id=second_slot.professional_id,
        slot_id=second_slot.slot_id,
        starts_at=second_slot.starts_at,
    )
    with pytest.raises(DomainError) as conflict:
        scheduling_db["bookings"].reschedule(
            first.booking.business_id,
            first.booking.customer.id,
            first.booking.id,
            occupied_request,
            "failed-move",
        )
    assert conflict.value.code == "slot_unavailable"
    assert (
        scheduling_db["bookings"]
        .read(first.booking.business_id, first.booking.customer.id, first.booking.id)
        .starts_at
        == first_slot.starts_at
    )
    free_request = BookingRescheduleRequest(
        business_id=free_slot.business_id,
        service_id=free_slot.service_id,
        professional_id=free_slot.professional_id,
        slot_id=free_slot.slot_id,
        starts_at=free_slot.starts_at,
    )
    moved, outcome = scheduling_db["bookings"].reschedule(
        first.booking.business_id,
        first.booking.customer.id,
        first.booking.id,
        free_request,
        "successful-move",
    )
    assert outcome == "rescheduled"
    assert moved.starts_at == free_slot.starts_at
    assert scheduling_db["bookings"].reschedule(
        first.booking.business_id,
        first.booking.customer.id,
        first.booking.id,
        free_request,
        "successful-move",
    ) == (moved, outcome)
    assert (
        scheduling_db["bookings"]
        .read(second.booking.business_id, second.booking.customer.id, second.booking.id)
        .starts_at
        == second_slot.starts_at
    )


def test_cancel_releases_slot(scheduling_db):
    slot = offered_slots(scheduling_db)[0]
    created = create_booking(
        scheduling_db, slot, key="cancel-booking", phone="+5511999930001"
    )

    booking, outcome = scheduling_db["bookings"].cancel(
        created.booking.business_id,
        created.booking.customer.id,
        created.booking.id,
        "cancel-key",
    )

    assert outcome == "canceled"
    assert booking.status.value == "canceled"
    assert scheduling_db["bookings"].cancel(
        created.booking.business_id,
        created.booking.customer.id,
        created.booking.id,
        "cancel-key",
    ) == (booking, outcome)
    replacement = create_booking(
        scheduling_db, slot, key="replacement-booking", phone="+5511999930002"
    )
    assert replacement.booking.starts_at == slot.starts_at


def test_concurrent_same_key_replays_and_overlapping_creates_only_commit_once(
    scheduling_db,
):
    slots = offered_slots(scheduling_db)
    first_slot = slots[0]
    service = scheduling_db["bookings"]
    same_request = BookingCreateRequest(
        business_id=first_slot.business_id,
        service_id=first_slot.service_id,
        professional_id=first_slot.professional_id,
        slot_id=first_slot.slot_id,
        starts_at=first_slot.starts_at,
        customer_id=test_customer(scheduling_db, "+5511999960001"),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        same_key_results = list(
            executor.map(
                lambda _index: service.create(same_request, "concurrent-same-key"),
                range(2),
            )
        )
    assert len({result.booking.id for result in same_key_results}) == 1
    assert sorted(result.outcome for result in same_key_results) == [
        "created",
        "replayed",
    ]

    competing_slot = slot_at_offset(
        slots,
        professional_id=first_slot.professional_id,
        after=first_slot.starts_at,
        minutes=75,
    )

    def competing_create(index):
        request = BookingCreateRequest(
            business_id=competing_slot.business_id,
            service_id=competing_slot.service_id,
            professional_id=competing_slot.professional_id,
            slot_id=competing_slot.slot_id,
            starts_at=competing_slot.starts_at,
            customer_id=test_customer(scheduling_db, f"+551199996000{index + 2}"),
        )
        try:
            return service.create(request, f"concurrent-key-{index}")
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        competing_results = list(executor.map(competing_create, range(2)))
    assert sum(isinstance(result, BookingSummary) for result in competing_results) == 1
    failures = [
        result for result in competing_results if isinstance(result, DomainError)
    ]
    assert len(failures) == 1
    assert failures[0].code == "slot_unavailable"
    with connect(scheduling_db["url"]) as connection:
        count = connection.execute(
            "SELECT count(*) AS n FROM bookings WHERE status IN ('scheduled','confirmed')"
        ).fetchone()["n"]
    assert count == 2


def test_booking_and_retry_record_roll_back_together(scheduling_db):
    import psycopg
    from sqlalchemy.exc import DBAPIError

    slot = offered_slots(scheduling_db)[0]
    request = BookingCreateRequest(
        business_id=slot.business_id,
        service_id=slot.service_id,
        professional_id=slot.professional_id,
        slot_id=slot.slot_id,
        starts_at=slot.starts_at,
        customer_id=test_customer(scheduling_db, "+5511999970001"),
    )
    with connect(scheduling_db["url"]) as connection:
        connection.execute("""CREATE FUNCTION fail_retry_insert() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'simulated retry record failure'; END $$""")
        connection.execute("""CREATE TRIGGER fail_retry_insert_trigger
            BEFORE INSERT ON idempotency_records FOR EACH ROW EXECUTE FUNCTION fail_retry_insert()""")
    try:
        with pytest.raises(DBAPIError) as error:
            scheduling_db["bookings"].create(request, "rollback-create")
        assert isinstance(error.value.orig, psycopg.errors.RaiseException)
        with connect(scheduling_db["url"]) as connection:
            assert (
                connection.execute("SELECT count(*) AS n FROM customers").fetchone()[
                    "n"
                ]
                == 1
            )
            assert (
                connection.execute("SELECT count(*) AS n FROM bookings").fetchone()["n"]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT count(*) AS n FROM idempotency_records"
                ).fetchone()["n"]
                == 0
            )
    finally:
        with connect(scheduling_db["url"]) as connection:
            connection.execute(
                "DROP TRIGGER IF EXISTS fail_retry_insert_trigger ON idempotency_records"
            )
            connection.execute("DROP FUNCTION IF EXISTS fail_retry_insert()")
