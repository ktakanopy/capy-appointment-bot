# Security and data handling

This repository is a synthetic-data local portfolio demo, not a production booking service.

## Booking management capability

- Each booking receives a capability token derived from `BOOKING_TOKEN_SECRET`, booking ID, and token version using HMAC-SHA-256.
- The token is returned only when a booking is created or replayed with the same idempotency key. The database stores a SHA-256 digest, not the token.
- Management reads and mutations require the token in `X-Booking-Token` or the separate `manage_token` field on `/chat`. An opaque database ID alone grants no access.
- Unknown bookings, malformed tokens, and invalid tokens share the same `booking_not_found` response.
- A secret with at least 32 random bytes is required to create or use management tokens. Rotating it invalidates outstanding tokens; the demo has no token-recovery flow.

## Conversation boundary

The API removes any token-shaped text from a message before passing it to LangGraph. The token supplied separately for a management action is not included in graph input, checkpoint state, model input, logs, traces, URLs, or error messages. The worker has no graph or checkpointer dependency.

The language model has no write tools. It extracts a typed intent only. Python services validate slot policy, booking ownership through the capability, future-time rules, and allowed status transitions.

## Concurrency and idempotency

- Booking transactions lock the professional and recheck service assignment, weekly hours, notice/horizon, blocks, and active bookings.
- A PostgreSQL GiST exclusion constraint rejects overlapping occupied intervals for scheduled or confirmed bookings. The interval includes service buffers and is half-open, so adjacent bookings can touch exactly at a boundary.
- `Idempotency-Key` is required on booking writes and scoped by business and operation. Reusing a key with a different request hash returns a conflict.
- Create plus pending reminder, cancel plus pending-reminder cancellation, and reschedule plus reminder adjustment are transactional.
- The reschedule update is atomic; a failed move leaves the old booking unchanged.
- The reminder worker claims due rows using `FOR UPDATE SKIP LOCKED` and the inbox has a unique constraint on `reminder_id`.

## Logs and traces

Application logs and Langfuse metadata include intent, outcome, slot count, and stable resource IDs. Names, phone numbers, email addresses, raw messages, and capability tokens are excluded or redacted. Langfuse is optional and uses a database separate from the app database.

## Demo limitations

The owner agenda is unauthenticated and should only be used with local synthetic data. The customer UI stores the management token in the current Streamlit session so the user can complete the demo. Use TLS, real owner authentication, secret management, retention controls, rate limiting, and a reviewed privacy model before adapting this to real customers.
