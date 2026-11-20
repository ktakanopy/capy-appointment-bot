"""Prepare the massage studio schema for a fresh database."""

from alembic import op

revision = "0007_massage_studio"
down_revision = "0006_telegram_publications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM businesses) THEN
                RAISE EXCEPTION 'Use a fresh database for the massage studio schema';
            END IF;
        END $$;

        DROP TABLE professional_services;
        ALTER TABLE professionals RENAME TO massagistas;
        CREATE UNIQUE INDEX one_massagista_per_business ON massagistas (business_id);

        ALTER TABLE services DROP COLUMN name;
        ALTER TABLE services ADD COLUMN service_type text NOT NULL
            CHECK (length(trim(service_type)) > 0 AND service_type = trim(service_type));
        ALTER TABLE services ADD COLUMN name text
            GENERATED ALWAYS AS (service_type || ' ' || duration_minutes::text || ' min') STORED;
        CREATE UNIQUE INDEX unique_service_duration
            ON services (business_id, lower(service_type), duration_minutes);

        ALTER TABLE telegram_outbox DROP COLUMN reminder_id CASCADE;
        ALTER TABLE telegram_outbox DROP COLUMN reminder_generation CASCADE;
        ALTER TABLE telegram_outbox ALTER COLUMN update_id SET NOT NULL;
        ALTER TABLE telegram_bot_delivery DROP COLUMN reply_streak;
        ALTER TABLE telegram_bots DROP COLUMN reminder_delivery;
        ALTER TABLE bookings DROP COLUMN telegram_bot_id;
        ALTER TABLE bookings DROP COLUMN telegram_chat_id;
        ALTER TABLE businesses DROP COLUMN reminder_lead_minutes;
        DROP TABLE reminder_inbox;
        DROP TABLE reminders;
    """)


def downgrade() -> None:
    raise RuntimeError("Restore the previous application with its matching database.")
