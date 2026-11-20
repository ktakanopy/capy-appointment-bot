"""Create the general scheduling schema.

Revision ID: 0001_general_scheduling
Revises:
Create Date: 2026-10-19
"""

from alembic import op

revision = "0001_general_scheduling"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("""
        CREATE TABLE businesses (
            id text PRIMARY KEY,
            name text NOT NULL,
            category text NOT NULL,
            timezone text NOT NULL,
            minimum_notice_minutes integer NOT NULL DEFAULT 60 CHECK (minimum_notice_minutes >= 0),
            booking_horizon_days integer NOT NULL DEFAULT 60 CHECK (booking_horizon_days > 0),
            reminder_lead_minutes integer NOT NULL DEFAULT 1440 CHECK (reminder_lead_minutes >= 0),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE professionals (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            name text NOT NULL,
            active boolean NOT NULL DEFAULT true,
            UNIQUE (id, business_id)
        );
        CREATE TABLE services (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            name text NOT NULL,
            description text NOT NULL DEFAULT '',
            duration_minutes integer NOT NULL CHECK (duration_minutes > 0),
            buffer_before_minutes integer NOT NULL DEFAULT 0 CHECK (buffer_before_minutes >= 0),
            buffer_after_minutes integer NOT NULL DEFAULT 0 CHECK (buffer_after_minutes >= 0),
            price_minor integer NOT NULL CHECK (price_minor >= 0),
            currency char(3) NOT NULL DEFAULT 'BRL',
            active boolean NOT NULL DEFAULT true,
            UNIQUE (id, business_id)
        );
        CREATE TABLE professional_services (
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            professional_id text NOT NULL REFERENCES professionals(id) ON DELETE CASCADE,
            service_id text NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            PRIMARY KEY (professional_id, service_id)
        );
        CREATE TABLE weekly_availability_rules (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            professional_id text NOT NULL REFERENCES professionals(id) ON DELETE CASCADE,
            weekday smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
            opens_at time NOT NULL,
            closes_at time NOT NULL,
            CHECK (closes_at > opens_at)
        );
        CREATE TABLE blocked_periods (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            professional_id text NOT NULL REFERENCES professionals(id) ON DELETE CASCADE,
            starts_at timestamptz NOT NULL,
            ends_at timestamptz NOT NULL,
            reason text NOT NULL DEFAULT '',
            CHECK (ends_at > starts_at)
        );
        CREATE INDEX blocked_periods_professional_range ON blocked_periods USING gist (professional_id, tstzrange(starts_at, ends_at, '[)'));
        CREATE TABLE customers (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            full_name text NOT NULL,
            phone text NOT NULL,
            email text,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, phone)
        );
        CREATE TABLE bookings (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id),
            service_id text NOT NULL REFERENCES services(id),
            professional_id text NOT NULL REFERENCES professionals(id),
            customer_id text NOT NULL REFERENCES customers(id),
            starts_at timestamptz NOT NULL,
            ends_at timestamptz NOT NULL,
            occupied_from timestamptz NOT NULL,
            occupied_until timestamptz NOT NULL,
            status text NOT NULL CHECK (status IN ('scheduled', 'confirmed', 'canceled')),
            token_version integer NOT NULL DEFAULT 1,
            token_digest text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (ends_at > starts_at),
            CHECK (occupied_until > occupied_from)
        );
        ALTER TABLE bookings ADD CONSTRAINT bookings_no_professional_overlap
            EXCLUDE USING gist (
                professional_id WITH =,
                tstzrange(occupied_from, occupied_until, '[)') WITH &&
            ) WHERE (status IN ('scheduled', 'confirmed'));
        CREATE INDEX bookings_business_start ON bookings (business_id, starts_at) WHERE status <> 'canceled';
        CREATE INDEX bookings_professional_start ON bookings (professional_id, starts_at) WHERE status <> 'canceled';
        CREATE TABLE reminders (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id),
            booking_id text NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
            send_at timestamptz NOT NULL,
            status text NOT NULL CHECK (status IN ('pending', 'processing', 'delivered', 'failed', 'canceled')),
            attempts integer NOT NULL DEFAULT 0,
            claimed_at timestamptz,
            delivered_at timestamptz,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX reminders_due ON reminders (send_at) WHERE status = 'pending';
        CREATE UNIQUE INDEX one_pending_reminder_per_booking ON reminders (booking_id) WHERE status = 'pending';
        CREATE TABLE reminder_inbox (
            id text PRIMARY KEY,
            reminder_id text NOT NULL UNIQUE REFERENCES reminders(id) ON DELETE CASCADE,
            booking_id text NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
            business_id text NOT NULL REFERENCES businesses(id),
            message text NOT NULL,
            delivered_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE idempotency_records (
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            operation text NOT NULL,
            idempotency_key text NOT NULL,
            request_hash text NOT NULL,
            resource_id text NOT NULL,
            result_code text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (business_id, operation, idempotency_key)
        );
        CREATE TABLE audit_events (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id),
            booking_id text REFERENCES bookings(id) ON DELETE SET NULL,
            action text NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            occurred_at timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS audit_events;
        DROP TABLE IF EXISTS idempotency_records;
        DROP TABLE IF EXISTS reminder_inbox;
        DROP TABLE IF EXISTS reminders;
        DROP TABLE IF EXISTS bookings;
        DROP TABLE IF EXISTS customers;
        DROP TABLE IF EXISTS blocked_periods;
        DROP TABLE IF EXISTS weekly_availability_rules;
        DROP TABLE IF EXISTS professional_services;
        DROP TABLE IF EXISTS services;
        DROP TABLE IF EXISTS professionals;
        DROP TABLE IF EXISTS businesses;
    """)
