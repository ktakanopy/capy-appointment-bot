"""Local owner inspection; this is not a customer authorization surface."""

import argparse
from zoneinfo import ZoneInfo

from app.config import load_settings
from app.owner import demo_reminders, owner_agenda
from app.repositories import PostgresRepository


def _print_owner(repository: PostgresRepository, business_id: str) -> None:
    agenda = owner_agenda(repository, business_id)
    catalog = repository.catalog(agenda.business.id)
    service_names = {service.id: service.name for service in catalog.services}
    zone = ZoneInfo(agenda.business.timezone)
    print(f"\nAgenda: {agenda.business.name} ({agenda.business.timezone})")
    if not agenda.bookings:
        print("  no bookings")
    for booking in agenda.bookings:
        local = booking.starts_at.astimezone(zone)
        print(
            f"  {local:%Y-%m-%d %H:%M} {booking.status.value:<9} "
            f"{booking.customer.full_name} "
            f"service={service_names.get(booking.service_id, booking.service_id)} "
            f"reminder={booking.reminder_status or 'none'}"
        )
    print("Demo inbox:")
    reminders = demo_reminders(repository, business_id)
    if not reminders.reminders:
        print("  empty")
    for reminder in reminders.reminders[:5]:
        print(f"  {reminder.message}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reminder-lead-minutes", type=int)
    args = parser.parse_args()
    if (
        args.reminder_lead_minutes is not None
        and not 0 <= args.reminder_lead_minutes <= 10080
    ):
        parser.error("Reminder lead must be between 0 and 10080 minutes")
    settings = load_settings()
    repository = PostgresRepository(settings.database_url)
    if args.reminder_lead_minutes is not None:
        updated = repository.update_reminder_lead(
            settings.demo_business_id, args.reminder_lead_minutes
        )
        if updated is None:
            parser.error("Business not found")
    _print_owner(repository, settings.demo_business_id)


if __name__ == "__main__":
    main()
