import pytest

from app.channels.telegram_repository import TelegramRepository
from app.models import DomainError
from tests.integration.helpers import create_booking, offered_slots


def test_telegram_identity_is_stable_and_phone_never_merges_accounts(scheduling_db):
    repo = TelegramRepository(scheduling_db["url"])
    first = repo.ensure_customer_for_sender("demo-studio", 42, 101, "First", "first")
    again = repo.ensure_customer_for_sender(
        "demo-studio", 42, 101, "Renamed", "newname"
    )
    second = repo.ensure_customer_for_sender("demo-studio", 42, 102, "Second")
    assert first["customer_id"] == again["customer_id"]
    assert first["customer_id"] != second["customer_id"]
    for customer in (first, second):
        repo.save_contact("demo-studio", customer["customer_id"], "+5511999998888")
    assert (
        repo.ensure_customer_for_sender("demo-studio", 42, 102, "Second")["customer_id"]
        == second["customer_id"]
    )


def test_every_booking_operation_checks_customer_business_and_replay(scheduling_db):
    booking = create_booking(
        scheduling_db,
        offered_slots(scheduling_db)[0],
        key="owned",
        phone="+5511999912345",
    ).booking
    service = scheduling_db["bookings"]
    from app.models import BookingRescheduleRequest

    slot = offered_slots(scheduling_db)[-1]
    request = BookingRescheduleRequest(
        business_id=slot.business_id,
        service_id=slot.service_id,
        professional_id=slot.professional_id,
        slot_id=slot.slot_id,
        starts_at=slot.starts_at,
    )
    for business, customer in [
        (booking.business_id, "stranger"),
        ("other-business", booking.customer.id),
        (booking.business_id, None),
    ]:
        for method, args in [
            (service.read, ()),
            (service.confirm, ("key",)),
            (service.cancel, ("key",)),
            (service.reschedule, (request, "key")),
        ]:
            with pytest.raises(DomainError) as denied:
                method(business, customer, booking.id, *args)
            assert denied.value.code in {"booking_not_found", "business_not_found"}
    confirmed, _ = service.confirm(
        booking.business_id, booking.customer.id, booking.id, "confirm-replay"
    )
    with pytest.raises(DomainError):
        service.confirm(booking.business_id, "stranger", booking.id, "confirm-replay")
    assert confirmed.status.value == "confirmed"


def test_telegram_receipts_dedupe_and_recovery_never_reruns_unknown_write(
    scheduling_db,
):
    repo = TelegramRepository(scheduling_db["url"])
    update = dict(
        bot_id=42,
        update_id=100,
        business_id="demo-studio",
        chat_id=101,
        telegram_user_id=101,
        payload={"message": {"text": "book"}},
    )
    repo.save_update(**update)
    repo.save_update(**update)
    assert len(repo.pending_updates(42)) == 1
    assert repo.claim_update(42, 100)
    assert repo.claim_update(42, 100) is None
    repo.recover_interrupted(42)
    assert repo.pending_updates(42) == []
    assert "check your appointments" in repo.ready_replies(42)[0]["reply_text"]
    repo.mark_delivered(42, 100)
    repo.save_update(**update)
    assert repo.pending_updates(42) == []
