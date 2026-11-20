from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Booking, CatalogResponse, AvailabilitySlot

_PORTUGUESE_MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def booking_details(
    starts_at: datetime,
    service_name: str,
    professional_name: str,
    timezone_name: str,
    language: str,
) -> dict[str, str]:
    """Format appointment facts in the business timezone for confirmation replies.

    Both the consent question and saved-booking reply use this projection so
    the customer sees the same local date, time, and timezone at each step.
    """
    local_start = starts_at.astimezone(ZoneInfo(timezone_name))
    if language == "pt":
        date_label = (
            f"{local_start.day} de {_PORTUGUESE_MONTHS[local_start.month - 1]} "
            f"de {local_start.year}"
        )
    else:
        month = local_start.strftime("%B")
        date_label = f"{month} {local_start.day}, {local_start.year}"
    return {
        "service": service_name,
        "professional": professional_name,
        "date": date_label,
        "time": local_start.strftime("%H:%M"),
        "timezone": timezone_name,
    }


def booking_confirmation_question(
    slot: AvailabilitySlot, catalog: CatalogResponse, language: str
) -> str:
    """Ask the customer to approve one specific offered appointment before writing it."""
    service = next(
        (item.name for item in catalog.services if item.id == slot.service_id),
        "service",
    )
    facts = booking_details(
        slot.starts_at,
        service,
        slot.professional_name,
        catalog.business.timezone,
        language,
    )
    if language == "pt":
        return (
            f"Confirma {facts['service']} com {facts['professional']} em "
            f"{facts['date']} às {facts['time']} ({facts['timezone']})? "
            "Responda “sim” para agendar ou “não” para desistir."
        )
    return (
        f"Please confirm {facts['service']} with {facts['professional']} on "
        f"{facts['date']} at {facts['time']} ({facts['timezone']}). "
        "Reply “yes” to book or “no” to leave it unbooked."
    )


def booking_created_reply(
    booking: Booking,
    slot: AvailabilitySlot,
    catalog: CatalogResponse,
    language: str,
) -> str:
    """Report the committed booking's service, professional, local time, and timezone."""
    service = next(
        (item.name for item in catalog.services if item.id == booking.service_id),
        "service",
    )
    professional = next(
        (item.name for item in catalog.professionals if item.id == booking.professional_id),
        slot.professional_name,
    )
    facts = booking_details(
        booking.starts_at,
        service,
        professional,
        catalog.business.timezone,
        language,
    )
    if language == "pt":
        return (
            f"Agendamento confirmado: {facts['service']} com {facts['professional']} "
            f"em {facts['date']} às {facts['time']} ({facts['timezone']})."
        )
    return (
        f"Booked: {facts['service']} with {facts['professional']} on "
        f"{facts['date']} at {facts['time']} ({facts['timezone']})."
    )
