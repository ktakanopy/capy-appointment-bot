from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import streamlit as st

from app.config import load_settings
from frontend.lib.api_client import BackendClient


BUSINESS_ID = os.getenv("DEMO_BUSINESS_ID", "demo-studio")


def _client() -> BackendClient:
    return BackendClient(load_settings().frontend_api_base_url)


def _ensure_state() -> None:
    defaults = {
        "client": _client(),
        "session_id": None,
        "messages": [],
        "slots": [],
        "selected_slot": None,
        "booking": None,
        "manage_token": "",
        "loaded_manage_token": "",
        "catalog": None,
        "chat_error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _start_session() -> None:
    response = st.session_state.client.create_session()
    st.session_state.session_id = response["session_id"]
    st.session_state.messages = [{"role": "assistant", "content": response.get("response") or "Welcome. What would you like to book?"}]
    st.session_state.chat_error = None


def _error_text(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        try:
            detail = error.response.json().get("detail", {})
            if isinstance(detail, dict):
                return str(detail.get("message", "The request could not be completed."))
        except Exception:
            pass
    return "The scheduling service could not be reached. Check that the local API is running."


def _load_catalog() -> dict:
    try:
        result = st.session_state.client.catalog(BUSINESS_ID)
        st.session_state.catalog = result
        return result
    except httpx.HTTPError as error:
        st.error(_error_text(error))
        return st.session_state.catalog or {"business": {"name": "Demo studio"}, "services": [], "professionals": []}


def _customer_page(catalog: dict) -> None:
    business = catalog.get("business", {})
    st.subheader(f"Book with {business.get('name', 'the demo business')}")
    timezone_name = business.get("timezone", "America/Sao_Paulo")
    local_today = datetime.now(ZoneInfo(timezone_name)).date()
    services = catalog.get("services", [])
    if not services:
        st.info("The business catalog is empty. Run the database seed command first.")
        return

    service_by_label = {f"{service['name']} · {service['duration_minutes']} min": service for service in services}
    with st.form("availability-search"):
        service_label = st.selectbox("Service", list(service_by_label))
        search_date = st.date_input("Date", value=local_today, min_value=local_today)
        professionals = catalog.get("professionals", [])
        professional_labels = {"Any available professional": None}
        professional_labels.update({person["name"]: person["id"] for person in professionals})
        professional_label = st.selectbox("Professional", list(professional_labels))
        submitted = st.form_submit_button("Find available times", type="primary")
    if submitted:
        service = service_by_label[service_label]
        try:
            result = st.session_state.client.search_availability({
                "business_id": BUSINESS_ID,
                "service_id": service["id"],
                "professional_id": professional_labels[professional_label],
                "start_date": search_date.isoformat(),
                "end_date": search_date.isoformat(),
            })
            st.session_state.slots = result["slots"]
            st.session_state.selected_slot = None
        except httpx.HTTPError as error:
            st.error(_error_text(error))

    if st.session_state.slots:
        st.markdown("#### Available times")
        for index, slot in enumerate(st.session_state.slots, start=1):
            price = slot["price_minor"] / 100
            label = f"{slot['local_label']} · {slot['professional_name']} · {slot['currency']} {price:.2f}"
            columns = st.columns([5, 1])
            columns[0].write(label)
            has_manageable_booking = bool(st.session_state.manage_token and st.session_state.booking)
            action_label = "Move booking" if has_manageable_booking else "Choose"
            if columns[1].button(action_label, key=f"choose-{slot['slot_id']}"):
                if has_manageable_booking:
                    try:
                        result = st.session_state.client.reschedule({
                            "business_id": BUSINESS_ID,
                            "service_id": slot["service_id"],
                            "professional_id": slot["professional_id"],
                            "slot_id": slot["slot_id"],
                            "starts_at": slot["starts_at"],
                        }, st.session_state.manage_token)
                        st.session_state.booking = result["booking"]
                        st.success("Booking moved.")
                    except httpx.HTTPError as error:
                        st.error(_error_text(error))
                else:
                    st.session_state.selected_slot = slot

    if st.session_state.selected_slot:
        slot = st.session_state.selected_slot
        st.markdown(f"#### Book {slot['local_label']} with {slot['professional_name']}")
        with st.form("booking-details"):
            full_name = st.text_input("Full name")
            phone = st.text_input("Phone number", placeholder="+55 11 99999-8888")
            email = st.text_input("Email (optional)")
            create = st.form_submit_button("Confirm booking", type="primary")
        if create:
            try:
                result = st.session_state.client.create_booking({
                    "business_id": slot["business_id"],
                    "service_id": slot["service_id"],
                    "professional_id": slot["professional_id"],
                    "slot_id": slot["slot_id"],
                    "starts_at": slot["starts_at"],
                    "customer": {"full_name": full_name, "phone": phone, "email": email or None},
                })
                st.session_state.booking = result["booking"]
                st.session_state.manage_token = result.get("manage_token") or ""
                st.session_state.loaded_manage_token = st.session_state.manage_token
                st.session_state["manage-token-entry"] = st.session_state.manage_token
                st.session_state.selected_slot = None
                st.success("Booking created.")
            except httpx.HTTPError as error:
                st.error(_error_text(error))

    if st.session_state.booking:
        booking = st.session_state.booking
        st.markdown("#### Your booking")
        st.write(f"Status: **{booking['status']}**")
        st.write(f"Starts: {booking['starts_at']}")
        st.code(st.session_state.manage_token, language=None)
        st.caption("Save this private token. It is required to manage this booking.")

    st.divider()
    st.markdown("#### Conversation")
    if st.session_state.session_id is None:
        _start_session()
    for item in st.session_state.messages:
        with st.chat_message(item["role"]):
            st.write(item["content"])
    message = st.chat_input("Ask about a service or say what you want to change")
    if message:
        st.session_state.messages.append({"role": "user", "content": message})
        try:
            response = st.session_state.client.send_message(
                st.session_state.session_id,
                message,
                business_id=BUSINESS_ID,
                manage_token=st.session_state.manage_token or None,
            )
            st.session_state.messages.append({"role": "assistant", "content": response["response"]})
            st.session_state.slots = response.get("slots") or st.session_state.slots
            if response.get("manage_token"):
                st.session_state.manage_token = response["manage_token"]
            if response.get("booking"):
                st.session_state.booking = response["booking"]
            st.session_state.chat_error = None
        except httpx.HTTPError as error:
            st.session_state.chat_error = _error_text(error)
        st.rerun()
    if st.session_state.chat_error:
        st.error(st.session_state.chat_error)

    token = st.text_input("Booking management token", value=st.session_state.manage_token, type="password", key="manage-token-entry")
    if token != st.session_state.manage_token:
        st.session_state.manage_token = token
        if not token:
            st.session_state.loaded_manage_token = ""
            st.session_state.booking = None
    if token:
        if token != st.session_state.loaded_manage_token:
            try:
                st.session_state.booking = st.session_state.client.my_booking(token)
                st.session_state.loaded_manage_token = token
            except httpx.HTTPError as error:
                st.session_state.booking = None
                st.session_state.loaded_manage_token = ""
                st.error(_error_text(error))
        action_cols = st.columns(3)
        for column, action, label in zip(action_cols, ("confirm", "cancel", "reschedule"), ("Confirm", "Cancel", "Find new time")):
            if action != "reschedule" and column.button(label, key=f"manage-{action}"):
                try:
                    result = st.session_state.client.manage(action, token)
                    st.session_state.booking = result["booking"]
                    st.success(f"Booking {result['outcome']}.")
                except httpx.HTTPError as error:
                    st.error(_error_text(error))
            elif action == "reschedule" and column.button(label, key="manage-reschedule"):
                st.session_state.show_reschedule = True
        if st.session_state.get("show_reschedule"):
            booking = st.session_state.booking
            if booking:
                date_value = st.date_input("New date", value=local_today + timedelta(days=1), key="reschedule-date")
                if st.button("Find new times", key="find-reschedule-times"):
                    service = next((item for item in services if item["id"] == booking["service_id"]), None)
                    if service:
                        try:
                            found = st.session_state.client.search_availability({
                                "business_id": BUSINESS_ID,
                                "service_id": service["id"],
                                "start_date": date_value.isoformat(),
                                "end_date": date_value.isoformat(),
                            })
                            st.session_state.slots = found["slots"]
                        except httpx.HTTPError as error:
                            st.error(_error_text(error))
                for slot in st.session_state.slots:
                    if st.button(f"Move to {slot['local_label']} with {slot['professional_name']}", key=f"resched-{slot['slot_id']}"):
                        try:
                            result = st.session_state.client.reschedule({
                                "business_id": BUSINESS_ID,
                                "service_id": slot["service_id"],
                                "professional_id": slot["professional_id"],
                                "slot_id": slot["slot_id"],
                                "starts_at": slot["starts_at"],
                            }, token)
                            st.session_state.booking = result["booking"]
                            st.session_state.show_reschedule = False
                            st.success("Booking moved.")
                        except httpx.HTTPError as error:
                            st.error(_error_text(error))

    st.divider()
    st.markdown("#### Simulated reminder reply")
    try:
        reminder_items = st.session_state.client.reminders(BUSINESS_ID).get("reminders", [])
    except httpx.HTTPError as error:
        reminder_items = []
        st.caption(_error_text(error))
    if reminder_items:
        reminder_by_label = {
            f"{item['customer_name']} · {item['service_name']} · {item['starts_at']}": item
            for item in reminder_items
        }
        selected_reminder_label = st.selectbox("Reminder", list(reminder_by_label))
        reply = st.text_input("Reply", value="Can I reschedule this booking for tomorrow?")
        if st.button("Send reply through chat"):
            if not st.session_state.manage_token:
                st.error("Enter the booking's private management token first.")
            else:
                try:
                    selected_reminder = reminder_by_label[selected_reminder_label]
                    authorized_booking = st.session_state.client.my_booking(st.session_state.manage_token)
                    if authorized_booking["id"] != selected_reminder["booking_id"]:
                        st.error("The management token does not match this reminder's booking.")
                        return
                    st.session_state.messages.append({"role": "user", "content": reply})
                    response = st.session_state.client.send_message(
                        st.session_state.session_id,
                        reply,
                        business_id=BUSINESS_ID,
                        manage_token=st.session_state.manage_token,
                    )
                    st.session_state.messages.append({"role": "assistant", "content": response["response"]})
                    st.session_state.slots = response.get("slots") or []
                    st.success("Reply sent as a new chat turn.")
                    st.rerun()
                except httpx.HTTPError as error:
                    st.error(_error_text(error))
    else:
        st.caption("No delivered demo reminders yet. The reminder worker places due items here.")


def _owner_page(catalog: dict) -> None:
    st.subheader("Owner agenda")
    st.caption("Read-only local demo view for the seeded business.")
    timezone_name = catalog.get("business", {}).get("timezone", "America/Sao_Paulo")
    local_today = datetime.now(ZoneInfo(timezone_name)).date()
    date_range = st.date_input("Date range", value=(local_today, local_today + timedelta(days=7)))
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range
    if st.button("Refresh agenda") or "owner_agenda" not in st.session_state:
        try:
            result = st.session_state.client.owner_bookings(BUSINESS_ID, start_date.isoformat(), end_date.isoformat())
            st.session_state.owner_agenda = result.get("bookings", [])
        except httpx.HTTPError as error:
            st.error(_error_text(error))
            st.session_state.owner_agenda = []
    if st.session_state.get("owner_agenda"):
        st.dataframe(st.session_state.owner_agenda, use_container_width=True, hide_index=True)
    else:
        st.info("No bookings in this date range.")

    st.markdown("#### Demo reminder inbox")
    try:
        reminders = st.session_state.client.reminders(BUSINESS_ID).get("reminders", [])
        if reminders:
            st.dataframe(reminders, use_container_width=True, hide_index=True)
        else:
            st.caption("The inbox is empty.")
    except httpx.HTTPError as error:
        st.error(_error_text(error))


def main() -> None:
    st.set_page_config(page_title="Service Scheduling", page_icon="📅", layout="wide")
    _ensure_state()
    st.title("Service Scheduling")
    st.caption("A local demo of reliable scheduling with AI-assisted interpretation and deterministic booking rules.")
    catalog = _load_catalog()
    customer_tab, owner_tab = st.tabs(["Customer", "Owner"])
    with customer_tab:
        _customer_page(catalog)
    with owner_tab:
        _owner_page(catalog)


if __name__ == "__main__":
    main()
