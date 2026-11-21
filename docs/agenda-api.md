# Private scheduling API

The FastAPI service exposes the same scheduling rules the Telegram worker uses.
Both write to the studio's PostgreSQL calendar. The API is scoped to
`DEMO_BUSINESS_ID` and is intended for an owner or a trusted application server.

Set `AGENDA_API_TOKEN` to a private random value of at least 32 characters,
then include it as `Authorization: Bearer <token>`. The routes are documented
in FastAPI's `/docs` view.

| Method and path | Purpose |
| --- | --- |
| `GET /agenda/catalog` | List services, durations and the professional. |
| `GET /agenda/slots` | Find available times for one `service_id`, `start_date` and `end_date`. Dates are local to the studio. |
| `GET /agenda/bookings` | View the owner agenda. Optionally pass `start_date` and `end_date`. |
| `POST /agenda/bookings` | Book a slot for an existing CAPY `customer_id`. |
| `POST /agenda/bookings/{booking_id}/reschedule` | Move a booking to another available slot. |
| `POST /agenda/bookings/{booking_id}/cancel` | Cancel a booking and release its time. |

Use the slot's `slot_id`, `service_id`, `professional_id` and `starts_at` from the
availability response when creating or moving a booking. Supply the existing
customer ID on create. Customers are linked when they first interact with the
Telegram bot. Every write requires an `Idempotency-Key` header. Reuse that key
when retrying the same operation. The booking service checks the slot again
before it commits and returns HTTP 409 if the slot has been taken.

```sh
curl --fail -H "Authorization: Bearer ${AGENDA_API_TOKEN}" \
  http://localhost:8000/agenda/catalog
```

[API routes](../app/agenda_api.py) · [Booking rules](../app/booking.py)
