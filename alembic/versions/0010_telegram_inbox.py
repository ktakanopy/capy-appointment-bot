"""Rename the Telegram update queue to telegram_inbox."""

from alembic import op

revision = "0010_telegram_inbox"
down_revision = "0009_telegram_postgres_queue"
branch_labels = None
depends_on = None


def _rename_inbox(old_name: str, new_name: str) -> None:
    op.rename_table(old_name, new_name)
    for suffix in ("pkey", "business_id_fkey", "status_check"):
        op.execute(
            f"ALTER TABLE {new_name} RENAME CONSTRAINT {old_name}_{suffix} "
            f"TO {new_name}_{suffix}"
        )
    for suffix in ("received", "chat_work", "expiry"):
        op.execute(f"ALTER INDEX {old_name}_{suffix} RENAME TO {new_name}_{suffix}")


def upgrade() -> None:
    _rename_inbox("telegram_updates", "telegram_inbox")


def downgrade() -> None:
    _rename_inbox("telegram_inbox", "telegram_updates")
