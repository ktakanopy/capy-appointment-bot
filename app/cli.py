"""Local owner inspection; this is not a customer authorization surface."""

from zoneinfo import ZoneInfo

from app.config import load_settings
from app.models import DomainError
from app.observability import debug_event
from app.owner import owner_agenda
from app.repositories import PostgresRepository


def _print_owner(repository: PostgresRepository, business_id: str) -> None:
    catalog = repository.catalog(business_id)
    if catalog is None:
        raise DomainError("business_not_found", "Business not found.", 404)
    agenda = owner_agenda(repository, catalog)
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
            f"service={service_names.get(booking.service_id, booking.service_id)}"
        )


def main():
    settings = load_settings()
    repository = PostgresRepository(settings.database_url)
    debug_event("owner.cli.started")
    _print_owner(repository, settings.demo_business_id)


if __name__ == "__main__":
    main()
