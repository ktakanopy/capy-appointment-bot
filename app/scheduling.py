from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import AvailabilitySearchRequest, AvailabilitySlot, DomainError
from app.repositories import PostgresRepository


UTC = timezone.utc
CANDIDATE_STEP_MINUTES = 15


def _valid_local_instants(wall_time: datetime, zone: ZoneInfo) -> list[datetime]:
    """Return all real instants represented by a naive local wall time."""
    instants: dict[datetime, datetime] = {}
    for fold in (0, 1):
        local = wall_time.replace(tzinfo=zone, fold=fold)
        instant = local.astimezone(UTC)
        round_trip = instant.astimezone(zone)
        if round_trip.replace(tzinfo=None) == wall_time and round_trip.fold == fold:
            instants[instant] = local
    return [instants[key] for key in sorted(instants)]


def _overlaps(start: datetime, end: datetime, intervals: list[tuple[datetime, datetime]]) -> bool:
    return any(start < interval_end and end > interval_start for interval_start, interval_end in intervals)


def validate_slot_in_transaction(
    connection,
    *,
    business_id: str,
    service_id: str,
    professional_id: str,
    slot_id: str,
    starts_at: datetime,
    exclude_booking_id: str | None = None,
) -> AvailabilitySlot:
    """Recheck current policy and conflicts in the same transaction as a booking write."""
    professional = connection.execute(
        "SELECT id FROM professionals WHERE id=%s AND business_id=%s AND active FOR UPDATE",
        (professional_id, business_id),
    ).fetchone()
    if professional is None:
        raise DomainError("professional_not_found", "Professional not found.", 404)
    row = connection.execute(
        """SELECT b.timezone,b.minimum_notice_minutes,b.booking_horizon_days,
                  s.duration_minutes,s.buffer_before_minutes,s.buffer_after_minutes,s.price_minor,s.currency,
                  p.name AS professional_name
           FROM businesses b JOIN services s ON s.business_id=b.id
           JOIN professional_services ps ON ps.business_id=b.id AND ps.service_id=s.id
           JOIN professionals p ON p.id=ps.professional_id
           WHERE b.id=%s AND s.id=%s AND s.active AND p.id=%s AND p.active""",
        (business_id, service_id, professional_id),
    ).fetchone()
    if row is None:
        raise DomainError("service_or_professional_not_found", "Service or professional not found.", 404)
    zone = ZoneInfo(row["timezone"])
    start = starts_at.astimezone(UTC)
    local_start = start.astimezone(zone)
    end = start + timedelta(minutes=row["duration_minutes"])
    occupied_from = start - timedelta(minutes=row["buffer_before_minutes"])
    occupied_until = end + timedelta(minutes=row["buffer_after_minutes"])
    now = datetime.now(UTC)
    if start < now + timedelta(minutes=row["minimum_notice_minutes"]):
        raise DomainError("slot_too_soon", "That time is inside the minimum booking notice.", 409)
    if start > now + timedelta(days=row["booking_horizon_days"]):
        raise DomainError("slot_outside_horizon", "That time is outside the booking horizon.", 409)
    if local_start.second or local_start.microsecond:
        raise DomainError("invalid_slot", "Choose one of the listed availability slots.", 409)
    rules = connection.execute(
        "SELECT opens_at,closes_at FROM weekly_availability_rules WHERE professional_id=%s AND weekday=%s",
        (professional_id, local_start.weekday()),
    ).fetchall()
    start_wall = local_start.timetz().replace(tzinfo=None)
    occupied_from_local = occupied_from.astimezone(zone)
    occupied_until_local = occupied_until.astimezone(zone)
    inside_hours = any(
        occupied_from_local.date() == local_start.date()
        and occupied_until_local.date() == local_start.date()
        and occupied_from_local.timetz().replace(tzinfo=None) >= rule["opens_at"]
        and occupied_until_local.timetz().replace(tzinfo=None) <= rule["closes_at"]
        and (datetime.combine(local_start.date(), start_wall) - datetime.combine(local_start.date(), rule["opens_at"])).total_seconds()
        % (CANDIDATE_STEP_MINUTES * 60) == 0
        for rule in rules
    )
    if not inside_hours:
        raise DomainError("outside_business_hours", "That time is outside the professional's working hours.", 409)
    blocked = connection.execute(
        """SELECT 1 FROM blocked_periods WHERE professional_id=%s
           AND starts_at < %s AND ends_at > %s LIMIT 1""",
        (professional_id, occupied_until, occupied_from),
    ).fetchone()
    if blocked:
        raise DomainError("slot_blocked", "That time has been blocked. Choose another slot.", 409)
    conflict_sql = """SELECT 1 FROM bookings WHERE professional_id=%s AND status IN ('scheduled','confirmed')
                      AND occupied_from < %s AND occupied_until > %s"""
    conflict_params = [professional_id, occupied_until, occupied_from]
    if exclude_booking_id:
        conflict_sql += " AND id <> %s"
        conflict_params.append(exclude_booking_id)
    if connection.execute(conflict_sql + " LIMIT 1", conflict_params).fetchone():
        raise DomainError("slot_unavailable", "That time is no longer available. Choose another slot.", 409)
    expected_slot_id = str(uuid5(
        NAMESPACE_URL,
        f"scheduling-slot:{business_id}:{service_id}:{professional_id}:{start.isoformat()}",
    ))
    if slot_id != expected_slot_id:
        raise DomainError("invalid_slot", "Choose one of the listed availability slots.", 409)
    label = local_start.strftime("%a %d %b, %H:%M %Z (UTC%z)")
    return AvailabilitySlot(
        slot_id=expected_slot_id,
        business_id=business_id,
        service_id=service_id,
        professional_id=professional_id,
        professional_name=row["professional_name"],
        starts_at=start,
        ends_at=end,
        local_label=label,
        duration_minutes=row["duration_minutes"],
        price_minor=row["price_minor"],
        currency=row["currency"].strip(),
    )


class AvailabilityService:
    def __init__(self, repository: PostgresRepository, now=None):
        self.repository = repository
        self._now = now or (lambda: datetime.now(UTC))

    def search(self, request: AvailabilitySearchRequest, *, exclude_booking_id: str | None = None) -> list[AvailabilitySlot]:
        business = self.repository.business(request.business_id)
        service = self.repository.service(request.business_id, request.service_id)
        if business is None:
            raise DomainError("business_not_found", "Business not found.", 404)
        if service is None:
            raise DomainError("service_not_found", "Service not found.", 404)
        try:
            zone = ZoneInfo(business.timezone)
        except ZoneInfoNotFoundError as exc:
            raise DomainError("invalid_business_timezone", "Business timezone is not configured.", 500) from exc

        now = self._now().astimezone(UTC)
        local_today = now.astimezone(zone).date()
        lower_date = max(request.start_date, local_today)
        upper_date = min(request.end_date, local_today + timedelta(days=business.booking_horizon_days))
        if upper_date < lower_date:
            return []
        from_local = datetime.combine(lower_date, time.min, zone) - timedelta(days=1)
        until_local = datetime.combine(upper_date + timedelta(days=1), time.min, zone) + timedelta(days=1)
        _, _, schedules = self.repository.scheduling_data(
            request.business_id,
            service.id,
            request.professional_id,
            from_local.astimezone(UTC),
            until_local.astimezone(UTC),
            exclude_booking_id=exclude_booking_id,
        )
        min_start = now + timedelta(minutes=business.minimum_notice_minutes)
        max_start = now + timedelta(days=business.booking_horizon_days)
        slots: list[AvailabilitySlot] = []
        for schedule in schedules:
            professional = schedule["professional"]
            rules_by_weekday: dict[int, list[tuple[time, time]]] = {}
            for rule in schedule["rules"]:
                rules_by_weekday.setdefault(rule["weekday"], []).append((rule["opens_at"], rule["closes_at"]))
            blocked = [(row["starts_at"].astimezone(UTC), row["ends_at"].astimezone(UTC)) for row in schedule["blocks"]]
            occupied = [(row["occupied_from"].astimezone(UTC), row["occupied_until"].astimezone(UTC)) for row in schedule["bookings"]]
            for day_offset in range((upper_date - lower_date).days + 1):
                current_day = lower_date + timedelta(days=day_offset)
                for opens_at, closes_at in rules_by_weekday.get(current_day.weekday(), []):
                    cursor = datetime.combine(current_day, opens_at)
                    closes = datetime.combine(current_day, closes_at)
                    while cursor < closes:
                        for local_start in _valid_local_instants(cursor, zone):
                            start = local_start.astimezone(UTC)
                            end = start + timedelta(minutes=service.duration_minutes)
                            occupied_from = start - timedelta(minutes=service.buffer_before_minutes)
                            occupied_until = end + timedelta(minutes=service.buffer_after_minutes)
                            local_start_time = local_start.timetz().replace(tzinfo=None)
                            if request.earliest_local_time and local_start_time < request.earliest_local_time:
                                continue
                            if request.latest_local_time and local_start_time > request.latest_local_time:
                                continue
                            if start < min_start or start > max_start:
                                continue
                            # Keep the appointment and its buffers within one published work interval.
                            local_occupied_from = occupied_from.astimezone(zone)
                            local_occupied_until = occupied_until.astimezone(zone)
                            if local_occupied_from.date() != current_day or local_occupied_until.date() != current_day:
                                continue
                            if local_occupied_from.timetz().replace(tzinfo=None) < opens_at:
                                continue
                            if local_occupied_until.timetz().replace(tzinfo=None) > closes_at:
                                continue
                            if _overlaps(occupied_from, occupied_until, blocked):
                                continue
                            if _overlaps(occupied_from, occupied_until, occupied):
                                continue
                            slot_id = str(uuid5(
                                NAMESPACE_URL,
                                f"scheduling-slot:{request.business_id}:{service.id}:{professional['id']}:{start.isoformat()}",
                            ))
                            label = local_start.strftime("%a %d %b, %H:%M %Z (UTC%z)")
                            slots.append(AvailabilitySlot(
                                slot_id=slot_id,
                                business_id=request.business_id,
                                service_id=service.id,
                                professional_id=professional["id"],
                                professional_name=professional["name"],
                                starts_at=start,
                                ends_at=end,
                                local_label=label,
                                duration_minutes=service.duration_minutes,
                                price_minor=service.price_minor,
                                currency=service.currency.strip(),
                            ))
                        cursor += timedelta(minutes=CANDIDATE_STEP_MINUTES)
        unique_slots = {slot.slot_id: slot for slot in slots}
        return sorted(unique_slots.values(), key=lambda slot: (slot.starts_at, slot.professional_name))

    def validate_slot(
        self,
        *,
        business_id: str,
        service_id: str,
        professional_id: str,
        slot_id: str,
        starts_at: datetime,
        exclude_booking_id: str | None = None,
    ) -> AvailabilitySlot:
        if starts_at.tzinfo is None or starts_at.utcoffset() is None:
            raise DomainError("invalid_instant", "Choose a time with an explicit timezone offset.")
        local_date = starts_at.astimezone(UTC).date()
        business = self.repository.business(business_id)
        if business is None:
            raise DomainError("business_not_found", "Business not found.", 404)
        local_date = starts_at.astimezone(ZoneInfo(business.timezone)).date()
        slots = self.search(AvailabilitySearchRequest(
            business_id=business_id,
            service_id=service_id,
            professional_id=professional_id,
            start_date=local_date,
            end_date=local_date,
        ), exclude_booking_id=exclude_booking_id)
        target = starts_at.astimezone(UTC)
        for slot in slots:
            if slot.slot_id == slot_id and slot.starts_at == target:
                return slot
        raise DomainError("slot_unavailable", "That time is no longer available. Choose another slot.", 409)
