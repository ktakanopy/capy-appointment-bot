from app.models import AvailabilitySlot, CatalogResponse


def _choose_service(catalog: CatalogResponse, phrase: str | None):
    """Resolve an exact service name from this business's catalog."""
    if not phrase:
        return catalog.services[0] if len(catalog.services) == 1 else None
    folded = " ".join(phrase.split()).casefold()
    return next(
        (
            service
            for service in catalog.services
            if " ".join(service.name.split()).casefold() == folded
        ),
        None,
    )


def _choose_professional(catalog: CatalogResponse, phrase: str | None):
    """Resolve an exact professional name, returning ``None`` if it is unknown."""
    if not phrase:
        return None
    folded = " ".join(phrase.split()).casefold()
    return next(
        (
            person
            for person in catalog.professionals
            if " ".join(person.name.split()).casefold() == folded
        ),
        None,
    )


def _slot_message(slots: list[AvailabilitySlot]) -> str:
    """Format search results as numbered choices, or explain when no times are available."""
    if not slots:
        return "I couldn't find an available time for that date. Try another date or professional."
    lines = [
        f"{index}. {slot.local_label} with {slot.professional_name}"
        for index, slot in enumerate(slots[:10], 1)
    ]
    return (
        "Here are the available times. Choose a time below or reply with its number:\n"
        + "\n".join(lines)
    )
