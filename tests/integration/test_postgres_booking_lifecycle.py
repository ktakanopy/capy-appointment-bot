from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from app.booking import _digest
from app.database import connect
from app.models import BookingCreateRequest, BookingRescheduleRequest, BookingSummary, CustomerInput, DomainError
from app.seed import seed
from tests.integration.helpers import create_booking, offered_slots, slot_at_offset


def test_migration_seed_repeatability_and_second_business_scope(scheduling_db):
    seed(include_second_business=True)
    seed(include_second_business=True)
    catalog = scheduling_db["repository"].catalog("demo-studio")

    assert catalog is not None
    assert len(catalog.professionals) == 2
    assert [service.id for service in catalog.services] == ["service-demo"]
    assert scheduling_db["repository"].catalog("second-demo-studio").services[0].id == "service-second"
    with connect(scheduling_db["url"]) as connection:
        assert connection.execute("SELECT count(*) AS n FROM businesses").fetchone()["n"] == 2
        assert connection.execute("SELECT count(*) AS n FROM weekly_availability_rules").fetchone()["n"] == 24


def test_create_idempotency_digest_and_postgres_overlap_constraint(scheduling_db):
    slots = offered_slots(scheduling_db)
    slot = slots[0]
    request = BookingCreateRequest(
        business_id=slot.business_id,
        service_id=slot.service_id,
        professional_id=slot.professional_id,
        slot_id=slot.slot_id,
        starts_at=slot.starts_at,
        customer=CustomerInput(full_name="First Customer", phone="+5511999910001"),
    )
    service = scheduling_db["bookings"]
    created = service.create(request, "create-replay-key")
    replay = service.create(request, "create-replay-key")

    assert replay.outcome == "replayed"
    assert replay.booking.id == created.booking.id
    assert replay.manage_token == created.manage_token
    with connect(scheduling_db["url"]) as connection:
        row = connection.execute(
            "SELECT token_digest FROM bookings WHERE id=%s", (created.booking.id,)
        ).fetchone()
        assert row["token_digest"] == _digest(created.manage_token)
        assert created.manage_token not in row["token_digest"]

    changed = request.model_copy(update={
        "customer": CustomerInput(full_name="Different Customer", phone="+5511999910002")
    })
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
                       starts_at,ends_at,occupied_from,occupied_until,status,token_version,token_digest)
                       VALUES (%s,'demo-studio','service-demo',%s,%s,%s,%s,%s,%s,%s,1,%s)""",
                    (
                        booking_id,
                        slot.professional_id,
                        customer_id,
                        overlapping_start,
                        overlapping_end,
                        overlapping_start,
                        overlapping_end + timedelta(minutes=15),
                        status,
                        "0" * 64,
                    ),
                )
        except psycopg.errors.ExclusionViolation:
            if status != "scheduled":
                raise
        else:
            assert status == "canceled"


def test_failed_reschedule_preserves_booking_and_reminder_then_success_moves_both(scheduling_db):
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
    first = create_booking(scheduling_db, first_slot, key="reschedule-first", phone="+5511999920001")
    second = create_booking(scheduling_db, second_slot, key="reschedule-second", phone="+5511999920002")

    with connect(scheduling_db["url"]) as connection:
        old_reminder = connection.execute(
            "SELECT id,send_at,status FROM reminders WHERE booking_id=%s", (first.booking.id,)
        ).fetchone()

    occupied_request = BookingRescheduleRequest(
        business_id=second_slot.business_id,
        service_id=second_slot.service_id,
        professional_id=second_slot.professional_id,
        slot_id=second_slot.slot_id,
        starts_at=second_slot.starts_at,
    )
    with pytest.raises(DomainError) as conflict:
        scheduling_db["bookings"].reschedule(first.manage_token, occupied_request, "failed-move")
    assert conflict.value.code == "slot_unavailable"
    assert scheduling_db["bookings"].read(first.manage_token).starts_at == first_slot.starts_at
    with connect(scheduling_db["url"]) as connection:
        unchanged_reminder = connection.execute(
            "SELECT id,send_at,status FROM reminders WHERE booking_id=%s", (first.booking.id,)
        ).fetchone()
    assert unchanged_reminder == old_reminder

    free_request = BookingRescheduleRequest(
        business_id=free_slot.business_id,
        service_id=free_slot.service_id,
        professional_id=free_slot.professional_id,
        slot_id=free_slot.slot_id,
        starts_at=free_slot.starts_at,
    )
    moved, outcome = scheduling_db["bookings"].reschedule(first.manage_token, free_request, "successful-move")
    assert outcome == "rescheduled"
    assert moved.starts_at == free_slot.starts_at
    with connect(scheduling_db["url"]) as connection:
        updated_reminder = connection.execute(
            "SELECT id,send_at,status FROM reminders WHERE booking_id=%s", (first.booking.id,)
        ).fetchone()
    assert updated_reminder["id"] == old_reminder["id"]
    assert updated_reminder["send_at"] != old_reminder["send_at"]
    assert updated_reminder["status"] == "pending"
    assert scheduling_db["bookings"].read(second.manage_token).starts_at == second_slot.starts_at


def test_cancel_cancels_reminder_and_releases_slot(scheduling_db):
    slot = offered_slots(scheduling_db)[0]
    created = create_booking(scheduling_db, slot, key="cancel-booking", phone="+5511999930001")

    booking, outcome = scheduling_db["bookings"].cancel(created.manage_token, "cancel-key")

    assert outcome == "canceled"
    assert booking.status.value == "canceled"
    with connect(scheduling_db["url"]) as connection:
        reminder = connection.execute(
            "SELECT status FROM reminders WHERE booking_id=%s", (created.booking.id,)
        ).fetchone()
    assert reminder["status"] == "canceled"
    replacement = create_booking(scheduling_db, slot, key="replacement-booking", phone="+5511999930002")
    assert replacement.booking.starts_at == slot.starts_at


def test_concurrent_same_key_replays_and_overlapping_creates_only_commit_once(scheduling_db):
    slots = offered_slots(scheduling_db)
    first_slot = slots[0]
    service = scheduling_db["bookings"]
    same_request = BookingCreateRequest(
        business_id=first_slot.business_id,
        service_id=first_slot.service_id,
        professional_id=first_slot.professional_id,
        slot_id=first_slot.slot_id,
        starts_at=first_slot.starts_at,
        customer=CustomerInput(full_name="Same Request", phone="+5511999960001"),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        same_key_results = list(executor.map(
            lambda _index: service.create(same_request, "concurrent-same-key"), range(2)
        ))
    assert len({result.booking.id for result in same_key_results}) == 1
    assert sorted(result.outcome for result in same_key_results) == ["created", "replayed"]
    assert same_key_results[0].manage_token == same_key_results[1].manage_token

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
            customer=CustomerInput(
                full_name=f"Competing Customer {index}",
                phone=f"+551199996000{index + 2}",
            ),
        )
        try:
            return service.create(request, f"concurrent-key-{index}")
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        competing_results = list(executor.map(competing_create, range(2)))
    assert sum(isinstance(result, BookingSummary) for result in competing_results) == 1
    failures = [result for result in competing_results if isinstance(result, DomainError)]
    assert len(failures) == 1
    assert failures[0].code == "slot_unavailable"
    with connect(scheduling_db["url"]) as connection:
        count = connection.execute(
            "SELECT count(*) AS n FROM bookings WHERE status IN ('scheduled','confirmed')"
        ).fetchone()["n"]
    assert count == 2


def test_booking_and_reminder_creation_roll_back_together(scheduling_db):
    import psycopg

    slot = offered_slots(scheduling_db)[0]
    request = BookingCreateRequest(
        business_id=slot.business_id,
        service_id=slot.service_id,
        professional_id=slot.professional_id,
        slot_id=slot.slot_id,
        starts_at=slot.starts_at,
        customer=CustomerInput(full_name="Rollback Customer", phone="+5511999970001"),
    )
    with connect(scheduling_db["url"]) as connection:
        connection.execute("""CREATE FUNCTION fail_booking_reminder_insert() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'simulated reminder write failure'; END $$""")
        connection.execute("""CREATE TRIGGER fail_booking_reminder_insert_trigger
            BEFORE INSERT ON reminders FOR EACH ROW EXECUTE FUNCTION fail_booking_reminder_insert()""")
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            scheduling_db["bookings"].create(request, "rollback-create")
        with connect(scheduling_db["url"]) as connection:
            assert connection.execute("SELECT count(*) AS n FROM customers").fetchone()["n"] == 0
            assert connection.execute("SELECT count(*) AS n FROM bookings").fetchone()["n"] == 0
            assert connection.execute("SELECT count(*) AS n FROM reminders").fetchone()["n"] == 0
            assert connection.execute("SELECT count(*) AS n FROM audit_events").fetchone()["n"] == 0
            assert connection.execute("SELECT count(*) AS n FROM idempotency_records").fetchone()["n"] == 0
    finally:
        with connect(scheduling_db["url"]) as connection:
            connection.execute("DROP TRIGGER IF EXISTS fail_booking_reminder_insert_trigger ON reminders")
            connection.execute("DROP FUNCTION IF EXISTS fail_booking_reminder_insert()")
