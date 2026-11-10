from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from app.chat_adapter import handle_chat
from app.models import ChatRequest, DomainError
from app.owner import demo_reminders, owner_agenda
from app.runtime import RuntimeContext, close_runtime, create_runtime


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


def main() -> None:
    logging.getLogger("service_scheduling").setLevel(logging.WARNING)
    runtime = create_runtime()
    settings = runtime.settings
    session_id, _ = runtime.create_session()
    manage_token: str | None = None
    offered = []

    print(f"CAPY-bot CLI — business={settings.demo_business_id}")
    print("Commands: /token <token>, /owner, /help, /quit")
    try:
        while True:
            try:
                line = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in {"/quit", "/exit"}:
                break
            if line == "/help":
                print("Commands: /token <token>, /owner, /help, /quit")
                continue
            if line.startswith("/token"):
                manage_token = line.partition(" ")[2].strip() or None
                print(
                    "Management token set."
                    if manage_token
                    else "Management token cleared."
                )
                continue
            if line == "/owner":
                try:
                    _print_owner(runtime)
                except DomainError as error:
                    print(f"error: {error.message}")
                continue

            selected_slot_id = None
            message = line
            if offered:
                try:
                    index = int(line) - 1
                except ValueError:
                    index = -1
                if 0 <= index < len(offered):
                    selected_slot_id = offered[index].slot_id
                    message = f"slot {index + 1}"
                elif line.isdigit():
                    print("Choose a number from the offered slots.")
                    continue

            try:
                reply = handle_chat(
                    runtime,
                    ChatRequest(
                        session_id=session_id,
                        business_id=settings.demo_business_id,
                        message=message,
                        manage_token=manage_token,
                        selected_slot_id=selected_slot_id,
                    ),
                    None,
                )
            except DomainError as error:
                print(f"error: {error.message}")
                continue

            print(f"bot> {reply.response}")
            if reply.manage_token:
                manage_token = reply.manage_token
                print(f"token> {reply.manage_token}")
            offered = reply.slots
    finally:
        close_runtime(runtime)


if __name__ == "__main__":
    main()
