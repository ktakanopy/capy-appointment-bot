"""Poll existing Telegram work directly from PostgreSQL."""

from alembic import op

revision = "0009_telegram_postgres_queue"
down_revision = "0008_telegram_customers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        LOCK TABLE telegram_updates, telegram_conversations, telegram_outbox,
            telegram_bots, telegram_publications, telegram_bot_delivery,
            telegram_chat_delivery IN ACCESS EXCLUSIVE MODE;

        UPDATE telegram_outbox AS reply
        SET ready_at = greatest(
            reply.ready_at,
            (SELECT due_at FROM telegram_publications WHERE outgoing_id = reply.id),
            (SELECT available_at FROM telegram_bot_delivery WHERE bot_id = reply.bot_id),
            (SELECT available_at FROM telegram_chat_delivery
             WHERE bot_id = reply.bot_id AND chat_id = reply.chat_id)
        )
        WHERE reply.status = 'ready';

        UPDATE telegram_updates
        SET status = 'delivered', payload = NULL, last_error = 'unsupported_legacy_update',
            expires_at = clock_timestamp() + interval '30 days'
        WHERE status IN ('received', 'processing')
          AND NOT coalesce(chat_id > 0 AND chat_id = telegram_user_id
                           AND payload->'message'->'chat'->>'type' = 'private', false);

        INSERT INTO telegram_conversations (bot_id, chat_id, business_id)
        SELECT DISTINCT bot_id, chat_id, business_id
        FROM telegram_updates WHERE status IN ('received', 'processing')
        ON CONFLICT DO NOTHING;

        DO $$ BEGIN
            IF (
                SELECT count(*) FROM (
                    SELECT bot_id, business_id FROM telegram_updates
                    WHERE status IN ('received', 'processing')
                    UNION
                    SELECT bot_id, business_id FROM telegram_outbox
                    WHERE status IN ('ready', 'dispatching')
                ) AS pending_scopes
            ) > 1 THEN
                RAISE EXCEPTION 'Finish pending work for other Telegram bots before enabling the single worker';
            END IF;
        END $$;

        ALTER TABLE telegram_conversations DROP CONSTRAINT telegram_conversations_bot_id_business_id_fkey;
        ALTER TABLE telegram_conversations ADD FOREIGN KEY (business_id)
            REFERENCES businesses(id) ON DELETE CASCADE;
        ALTER TABLE telegram_outbox DROP CONSTRAINT telegram_outbox_bot_id_business_id_fkey;
        DROP TABLE telegram_publications, telegram_bot_delivery, telegram_chat_delivery, telegram_bots;
        CREATE INDEX telegram_outbox_cooldown ON telegram_outbox (bot_id, business_id, ready_at)
            WHERE last_error IN ('telegram_api_429', 'telegram_http_429');
    """)


def downgrade() -> None:
    raise RuntimeError("Restore the database backup and matching application before reverting the Telegram transport.")
