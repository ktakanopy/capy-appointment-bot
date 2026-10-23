from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.models import AvailabilitySearchRequest, Business, Service
from app.scheduling import AvailabilityService, _valid_local_instants


class ScheduleRepository:
    def __init__(self, blocked=()):
        self.blocked = list(blocked)
        self.demo_business = Business(
            id="demo-studio",
            name="Demo Studio",
            category="wellness",
            timezone="UTC",
            minimum_notice_minutes=5,
            booking_horizon_days=60,
        )
        self.demo_service = Service(
            id="massage",
            business_id="demo-studio",
            name="Massage",
            description="A one hour massage.",
            duration_minutes=60,
            buffer_before_minutes=15,
            buffer_after_minutes=15,
            price_minor=10000,
        )

    def business(self, business_id):
        return self.demo_business if business_id == self.demo_business.id else None

    def service(self, business_id, service_id):
        if business_id == self.demo_business.id and service_id == self.demo_service.id:
            return self.demo_service
        return None

    def scheduling_data(self, _business_id, _service_id, _professional_id, _from_at, _until_at, **_kwargs):
        return None, None, [{
            "professional": {"id": "alex", "name": "Alex"},
            "rules": [{"weekday": 1, "opens_at": time(9), "closes_at": time(12)}],
            "blocks": self.blocked,
            "bookings": [],
        }]


def test_availability_observes_work_hours_buffers_and_blocked_periods():
    repo = ScheduleRepository(blocked=[{
        "starts_at": datetime(2026, 10, 20, 10, 30, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 20, 10, 45, tzinfo=timezone.utc),
    }])
    service = AvailabilityService(repo, now=lambda: datetime(2026, 10, 19, 8, tzinfo=timezone.utc))

    slots = service.search(AvailabilitySearchRequest(
        service_id="massage",
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 20),
    ))

    # A 15 minute pre-buffer keeps 09:00 from starting before opening; the
    # post-buffer must finish by noon. The blocked period also removes overlap.
    assert [slot.starts_at for slot in slots] == [datetime(2026, 10, 20, 9, 15, tzinfo=timezone.utc)]
    assert slots[0].ends_at == datetime(2026, 10, 20, 10, 15, tzinfo=timezone.utc)


def test_availability_excludes_existing_booking_occupied_interval():
    repo = ScheduleRepository()
    repo.scheduling_data = lambda *_args, **_kwargs: (None, None, [{
        "professional": {"id": "alex", "name": "Alex"},
        "rules": [{"weekday": 1, "opens_at": time(9), "closes_at": time(12)}],
        "blocks": [],
        "bookings": [{
            "occupied_from": datetime(2026, 10, 20, 10, tzinfo=timezone.utc),
            "occupied_until": datetime(2026, 10, 20, 11, tzinfo=timezone.utc),
        }],
    }])
    service = AvailabilityService(repo, now=lambda: datetime(2026, 10, 19, 8, tzinfo=timezone.utc))

    slots = service.search(AvailabilitySearchRequest(
        service_id="massage",
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 20),
    ))

    assert slots == []


def test_local_dst_gap_is_skipped_and_repeated_time_has_two_instants():
    zone = ZoneInfo("America/New_York")

    nonexistent = _valid_local_instants(datetime(2026, 3, 8, 2, 30), zone)
    repeated = _valid_local_instants(datetime(2026, 11, 1, 1, 30), zone)

    assert nonexistent == []
    assert len(repeated) == 2
    assert repeated[0].utcoffset() != repeated[1].utcoffset()
