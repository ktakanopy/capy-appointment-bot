"""Use Telegram customer ownership and retire booking capabilities.

Existing identity links and bookings are preserved. Unlinked legacy customers
remain owner-visible; a phone match never grants a new account access.
"""
from alembic import op

revision = "0003_telegram_ownership"
down_revision = "0002_telegram_channel"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE customers DROP CONSTRAINT customers_business_id_phone_key;
        ALTER TABLE customers ALTER COLUMN phone DROP NOT NULL;
        DROP INDEX telegram_identity_phone_once;
        ALTER TABLE telegram_identities RENAME COLUMN verified_phone TO contact_phone;
        ALTER TABLE telegram_identities RENAME COLUMN verified_name TO display_name;
        ALTER TABLE bookings DROP COLUMN token_version, DROP COLUMN token_digest;
    """)


def downgrade():
    raise RuntimeError("Restore the pre-migration database backup and matching application to recover token-based access.")
