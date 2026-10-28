"""Add Telegram account links and durable update/reply receipts.

Revision ID: 0002_telegram_channel
Revises: 0001_general_scheduling
Create Date: 2026-10-27
"""

from alembic import op

revision = "0002_telegram_channel"
down_revision = "0001_general_scheduling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE telegram_identities (
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            bot_id bigint NOT NULL,
            telegram_user_id bigint NOT NULL,
            username text,
            customer_id text REFERENCES customers(id) ON DELETE SET NULL,
            verified_phone text,
            verified_name text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (business_id, bot_id, telegram_user_id)
        );
        CREATE UNIQUE INDEX telegram_identity_customer_once
            ON telegram_identities (business_id, bot_id, customer_id)
            WHERE customer_id IS NOT NULL;
        CREATE UNIQUE INDEX telegram_identity_phone_once
            ON telegram_identities (business_id, bot_id, verified_phone)
            WHERE verified_phone IS NOT NULL;

        CREATE TABLE telegram_updates (
            bot_id bigint NOT NULL,
            update_id bigint NOT NULL,
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            chat_id bigint,
            telegram_user_id bigint,
            status text NOT NULL DEFAULT 'received'
                CHECK (status IN ('received','processing','reply_ready','delivered')),
            payload jsonb,
            reply_text text,
            reply_markup jsonb,
            attempts integer NOT NULL DEFAULT 0,
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            last_error text,
            received_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL DEFAULT now() + interval '7 days',
            PRIMARY KEY (bot_id, update_id)
        );
        CREATE INDEX telegram_updates_ready
            ON telegram_updates (bot_id, next_attempt_at, received_at)
            WHERE status = 'reply_ready';
        CREATE INDEX telegram_updates_received
            ON telegram_updates (bot_id, received_at)
            WHERE status IN ('received','processing');

        CREATE TABLE telegram_poll_offsets (
            bot_id bigint PRIMARY KEY,
            next_update_id bigint NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS telegram_poll_offsets;
        DROP TABLE IF EXISTS telegram_updates;
        DROP TABLE IF EXISTS telegram_identities;
    """)
