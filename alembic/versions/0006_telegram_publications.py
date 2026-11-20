"""Store due RabbitMQ publications alongside Telegram work."""

from alembic import op

revision = "0006_telegram_publications"
down_revision = "0005_telegram_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an indexed publication queue without altering existing jobs."""
    op.execute("""
        CREATE TABLE telegram_publications (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            bot_id bigint,
            chat_id bigint,
            outgoing_id bigint REFERENCES telegram_outbox(id) ON DELETE CASCADE,
            due_at timestamptz NOT NULL,
            claimed_until timestamptz,
            FOREIGN KEY (bot_id, chat_id)
                REFERENCES telegram_conversations(bot_id, chat_id) ON DELETE CASCADE,
            CHECK ((bot_id IS NOT NULL AND chat_id IS NOT NULL AND outgoing_id IS NULL)
                OR (bot_id IS NULL AND chat_id IS NULL AND outgoing_id IS NOT NULL)),
            UNIQUE NULLS NOT DISTINCT (bot_id, chat_id, outgoing_id)
        );
        CREATE INDEX telegram_publications_outgoing ON telegram_publications(outgoing_id)
            WHERE outgoing_id IS NOT NULL;
        CREATE INDEX telegram_publications_due ON telegram_publications(due_at, id);
    """)


def downgrade() -> None:
    """Require operators to keep publications available during worker rollback."""
    raise RuntimeError("Keep the additive publication table during worker rollback.")
