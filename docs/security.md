# Ownership and data handling

Telegram's numeric sender ID, scoped by business and bot, maps to the customer. Username and display name are metadata. A supplied phone number never grants access or merges customers.

Booking reads and changes require trusted business/customer IDs from the transport boundary. The application checks them against the booking inside the mutation transaction, including idempotent replay. The model never supplies these trusted IDs. Customer management tokens and public customer mutation routes are retired. Owner inspection is a local CLI; it has no public HTTP surface.

Contacts shared through Telegram are stored only for that account. They are optional and do not constitute account linking. Do not add a typed-phone shortcut for claiming old bookings. Legacy records without a trusted link remain owner-visible.

Known customer display names, recognizable phone/email strings and pasted legacy tokens are sanitized before model input and the first checkpoint write. Contact fields returned by models are discarded. Arbitrary names in free text cannot be reliably detected by this simple sanitizer; this is not a guarantee of named-entity anonymization. Audio itself reaches the configured Gemma transcription endpoint before transcript sanitization.

New checkpoints contain bounded history and expire with the session TTL. Old checkpoint namespaces expire under their existing TTL; use an explicitly scoped purge if immediate old-data removal is required. Business records persist in PostgreSQL. Telegram intake payloads are removed when the turn becomes reply-ready; deduplication receipts have bounded retention. Temporary decoded audio is cleaned up on success and failure.

Logs/traces contain safe event names, IDs and outcomes, not raw messages, audio, provider exception bodies or contact values. Provider failure makes no booking write. An interrupted or otherwise ambiguous turn reports uncertainty rather than claiming rollback.

Hosted webhooks resolve the tenant through a configured bot route and verify the bot's secret header before reading the update. Tokens and webhook secrets come from the deployment environment. The database stores references to them. Receipt and delivery outcomes have bounded retention. [Deployment and cutover](production.md)

## RabbitMQ message data

The broker receives a bot/chat ID pair or an outgoing record ID. Treat the bot/chat pair as customer-linked metadata. Message and reply bodies, files, contact details and tokens stay out of CAPY's broker envelopes. Configure `TELEGRAM_AMQP_URL` through the deployment secret store. Use a private virtual host, restricted credentials and encrypted transport for hosted connections. The webhook validates the bot and its secret in the API. Consumers read the current bot and work state from PostgreSQL before acting. [Broker envelope](../app/channels/telegram_broker.py#L62)
