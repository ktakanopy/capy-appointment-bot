# Ownership and data handling

This is a synthetic-data portfolio application. Telegram's numeric sender ID, scoped by business and bot, maps to the customer. Username and display name are metadata. A supplied phone number never grants access or merges customers.

Booking reads and changes require trusted business/customer IDs from the transport boundary. The application checks them against the booking inside the mutation transaction, including idempotent replay. The model never supplies these trusted IDs. Customer management tokens and public customer mutation routes are retired. Owner inspection is a local CLI; it has no public HTTP surface.

Contacts shared through Telegram are stored only for that account. They are optional and do not constitute account linking. Do not add a typed-phone shortcut for claiming old bookings. Legacy records without a trusted link remain owner-visible.

Known customer display names, recognizable phone/email strings and pasted legacy tokens are sanitized before model input and the first checkpoint write. Contact fields returned by models are discarded. Arbitrary names in free text cannot be reliably detected by this simple sanitizer; this is not a guarantee of named-entity anonymization. Audio itself reaches the configured Gemma transcription endpoint before transcript sanitization.

New checkpoints contain bounded history and expire with the session TTL. Old checkpoint namespaces expire under their existing TTL; use an explicitly scoped purge if immediate old-data removal is required. Business records persist in PostgreSQL. Telegram intake payloads are removed when the turn becomes reply-ready; deduplication receipts have bounded retention. Temporary decoded audio is cleaned up on success and failure.

Logs/traces contain safe event names, IDs and outcomes, not raw messages, audio, provider exception bodies or contact values. Provider failure makes no booking write. An interrupted or otherwise ambiguous turn reports uncertainty rather than claiming rollback.

Before upgrading an existing instance, stop old workers and back up the database. Token removal has no automatic downgrade; restore the matching backup and application to roll back. The cleanup does not migrate or reset the currently running demo database.
