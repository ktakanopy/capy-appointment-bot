"""Remove the unused audit table when it contains no records."""

from alembic import op

revision = "0004_remove_audit_events"
down_revision = "0003_telegram_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM audit_events) THEN
                RAISE EXCEPTION 'audit_events contains records. Review and preserve them before emptying the table and retrying.';
            END IF;
            DROP TABLE audit_events;
        END;
        $$
    """)


def downgrade() -> None:
    op.execute("""
        CREATE TABLE audit_events (
            id text PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id),
            booking_id text REFERENCES bookings(id) ON DELETE SET NULL,
            action text NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            occurred_at timestamptz NOT NULL DEFAULT now()
        );
    """)
