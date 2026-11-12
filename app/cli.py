"""Local owner inspection; this is not a customer authorization surface."""
import argparse
from zoneinfo import ZoneInfo
from app.owner import demo_reminders, owner_agenda
from app.runtime import create_runtime, close_runtime

def _print_owner(runtime: RuntimeContext) -> None:
    agenda = owner_agenda(runtime)
    catalog = runtime.repository.catalog(agenda.business.id)
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
    reminders = demo_reminders(runtime)
    if not reminders.reminders:
        print("  empty")
    for reminder in reminders.reminders[:5]:
        print(f"  {reminder.message}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reminder-lead-minutes", type=int)
    args = parser.parse_args()
    if args.reminder_lead_minutes is not None and not 0 <= args.reminder_lead_minutes <= 10080:
        parser.error("Reminder lead must be between 0 and 10080 minutes")
    runtime = create_runtime()
    try:
        if args.reminder_lead_minutes is not None:
            updated = runtime.repository.update_reminder_lead(runtime.settings.demo_business_id, args.reminder_lead_minutes)
            if updated is None:
                parser.error("Business not found")
        _print_owner(runtime)
    finally:
        close_runtime(runtime)

if __name__ == "__main__":
    main()
