"""Clarify Telegram processing and enforce business ownership of bookings."""

from alembic import op

revision = "0011_clarify_schema"
down_revision = "0010_telegram_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE telegram_conversations RENAME TO telegram_chat_processing_state;
        ALTER TABLE telegram_chat_processing_state RENAME CONSTRAINT
            telegram_conversations_pkey TO telegram_chat_processing_state_pkey;
        ALTER TABLE telegram_chat_processing_state RENAME CONSTRAINT
            telegram_conversations_business_id_fkey TO telegram_chat_processing_state_business_id_fkey;
        ALTER INDEX telegram_conversations_leased RENAME TO telegram_chat_processing_state_leased;

        ALTER TABLE telegram_inbox DROP CONSTRAINT telegram_inbox_status_check;
        UPDATE telegram_inbox SET status = 'processed' WHERE status = 'delivered';
        ALTER TABLE telegram_inbox ADD CONSTRAINT telegram_inbox_status_check
            CHECK (status IN ('received', 'processing', 'processed'));
        DROP INDEX telegram_inbox_expiry;
        CREATE INDEX telegram_inbox_expiry ON telegram_inbox (expires_at)
            WHERE status = 'processed';

        ALTER TABLE massagistas RENAME TO professionals;
        ALTER INDEX one_massagista_per_business RENAME TO one_professional_per_business;

        ALTER TABLE customers ADD CONSTRAINT customers_id_business_id_key UNIQUE (id, business_id);
        ALTER TABLE bookings
            DROP CONSTRAINT bookings_service_id_fkey,
            DROP CONSTRAINT bookings_professional_id_fkey,
            DROP CONSTRAINT bookings_customer_id_fkey,
            ADD CONSTRAINT bookings_service_business_fkey
                FOREIGN KEY (service_id, business_id) REFERENCES services (id, business_id),
            ADD CONSTRAINT bookings_professional_business_fkey
                FOREIGN KEY (professional_id, business_id) REFERENCES professionals (id, business_id),
            ADD CONSTRAINT bookings_customer_business_fkey
                FOREIGN KEY (customer_id, business_id) REFERENCES customers (id, business_id);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE bookings
            DROP CONSTRAINT bookings_service_business_fkey,
            DROP CONSTRAINT bookings_professional_business_fkey,
            DROP CONSTRAINT bookings_customer_business_fkey,
            ADD CONSTRAINT bookings_service_id_fkey FOREIGN KEY (service_id) REFERENCES services (id),
            ADD CONSTRAINT bookings_professional_id_fkey FOREIGN KEY (professional_id) REFERENCES professionals (id),
            ADD CONSTRAINT bookings_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES customers (id);
        ALTER TABLE customers DROP CONSTRAINT customers_id_business_id_key;

        ALTER INDEX one_professional_per_business RENAME TO one_massagista_per_business;
        ALTER TABLE professionals RENAME TO massagistas;

        ALTER TABLE telegram_inbox DROP CONSTRAINT telegram_inbox_status_check;
        UPDATE telegram_inbox SET status = 'delivered' WHERE status = 'processed';
        ALTER TABLE telegram_inbox ADD CONSTRAINT telegram_inbox_status_check
            CHECK (status IN ('received', 'processing', 'delivered'));
        DROP INDEX telegram_inbox_expiry;
        CREATE INDEX telegram_inbox_expiry ON telegram_inbox (expires_at)
            WHERE status = 'delivered';

        ALTER INDEX telegram_chat_processing_state_leased RENAME TO telegram_conversations_leased;
        ALTER TABLE telegram_chat_processing_state RENAME CONSTRAINT
            telegram_chat_processing_state_pkey TO telegram_conversations_pkey;
        ALTER TABLE telegram_chat_processing_state RENAME CONSTRAINT
            telegram_chat_processing_state_business_id_fkey TO telegram_conversations_business_id_fkey;
        ALTER TABLE telegram_chat_processing_state RENAME TO telegram_conversations;
    """)
