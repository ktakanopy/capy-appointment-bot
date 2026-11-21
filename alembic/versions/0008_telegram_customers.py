"""Store Telegram sender identities on their customer records."""

from alembic import op

revision = "0008_telegram_customers"
down_revision = "0007_massage_studio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        LOCK TABLE telegram_identities, telegram_updates IN ACCESS EXCLUSIVE MODE;

        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM telegram_updates WHERE status = 'reply_ready') THEN
                RAISE EXCEPTION 'Resolve legacy reply_ready updates before migrating Telegram customers';
            END IF;
            IF EXISTS (
                SELECT customer_id FROM telegram_identities
                WHERE customer_id IS NOT NULL
                GROUP BY customer_id HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'Resolve customers linked to more than one Telegram identity before upgrading';
            END IF;
            IF EXISTS (
                SELECT 1 FROM telegram_identities AS i
                JOIN customers AS c ON c.id = i.customer_id
                WHERE c.business_id <> i.business_id
            ) THEN
                RAISE EXCEPTION 'Resolve Telegram identities linked to customers in another business before upgrading';
            END IF;
        END $$;

        ALTER TABLE customers
            ADD COLUMN telegram_bot_id bigint,
            ADD COLUMN telegram_user_id bigint,
            ADD COLUMN telegram_username text,
            ADD COLUMN telegram_display_name text,
            ADD CONSTRAINT customers_telegram_sender_pair
                CHECK ((telegram_bot_id IS NULL) = (telegram_user_id IS NULL)),
            ADD CONSTRAINT customers_telegram_sender_key
                UNIQUE (business_id, telegram_bot_id, telegram_user_id);

        UPDATE customers AS c
        SET telegram_bot_id = i.bot_id,
            telegram_user_id = i.telegram_user_id,
            telegram_username = i.username,
            telegram_display_name = i.display_name
        FROM telegram_identities AS i
        WHERE c.id = i.customer_id;

        INSERT INTO customers (
            id, business_id, full_name, phone, created_at,
            telegram_bot_id, telegram_user_id, telegram_username, telegram_display_name
        )
        SELECT gen_random_uuid()::text, business_id,
               coalesce(nullif(display_name, ''), 'Telegram customer'), contact_phone, created_at,
               bot_id, telegram_user_id, username, display_name
        FROM telegram_identities
        WHERE customer_id IS NULL;

        DROP TABLE telegram_identities;
        DROP TABLE telegram_poll_offsets;

        ALTER TABLE telegram_updates
            DROP COLUMN reply_text,
            DROP COLUMN reply_markup,
            DROP COLUMN next_attempt_at,
            DROP CONSTRAINT telegram_updates_status_check,
            ADD CONSTRAINT telegram_updates_status_check
                CHECK (status IN ('received', 'processing', 'delivered'));
    """)


def downgrade() -> None:
    raise RuntimeError("Restore the database backup and matching application to recover the legacy Telegram schema.")
