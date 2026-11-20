"""Register Telegram bots and store conversation claims and outbound jobs."""

from alembic import op

revision = "0005_telegram_jobs"
down_revision = "0004_remove_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE telegram_bots (
            bot_id bigint PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            token_env text NOT NULL,
            webhook_route text UNIQUE,
            webhook_secret_env text,
            enabled boolean NOT NULL DEFAULT true,
            reminder_delivery boolean NOT NULL DEFAULT false,
            CHECK ((webhook_route IS NULL) = (webhook_secret_env IS NULL)),
            UNIQUE (bot_id, business_id)
        );
        ALTER TABLE telegram_updates ADD COLUMN message_date bigint;
        ALTER TABLE telegram_updates ADD COLUMN ready_at timestamptz NOT NULL DEFAULT now();
        CREATE INDEX telegram_updates_chat_work ON telegram_updates
            (bot_id, chat_id, message_date, update_id)
            WHERE status IN ('received', 'processing');
        CREATE INDEX telegram_updates_expiry ON telegram_updates (expires_at)
            WHERE status='delivered';
        CREATE TABLE telegram_conversations (
            bot_id bigint NOT NULL,
            chat_id bigint NOT NULL,
            business_id text NOT NULL,
            generation bigint NOT NULL DEFAULT 0,
            active_update_id bigint,
            lease_until timestamptz,
            last_claimed_at timestamptz,
            last_message_date bigint,
            last_update_id bigint,
            checkpoint_thread text,
            PRIMARY KEY (bot_id, chat_id),
            FOREIGN KEY (bot_id, business_id)
                REFERENCES telegram_bots (bot_id, business_id) ON DELETE CASCADE
        );
        CREATE INDEX telegram_conversations_leased ON telegram_conversations
            (business_id, lease_until)
            WHERE active_update_id IS NOT NULL;

        ALTER TABLE reminders ADD COLUMN generation bigint NOT NULL DEFAULT 1;
        ALTER TABLE reminders ADD COLUMN telegram_bot_id bigint;
        ALTER TABLE reminders ADD COLUMN telegram_chat_id bigint;
        ALTER TABLE reminders ADD COLUMN queued_generation bigint;
        ALTER TABLE bookings ADD COLUMN telegram_bot_id bigint;
        ALTER TABLE bookings ADD COLUMN telegram_chat_id bigint;
        ALTER TABLE bookings ADD CONSTRAINT bookings_telegram_destination_pair
            CHECK ((telegram_bot_id IS NULL) = (telegram_chat_id IS NULL));
        ALTER TABLE reminders ADD CONSTRAINT reminders_telegram_destination_pair
            CHECK ((telegram_bot_id IS NULL) = (telegram_chat_id IS NULL));
        CREATE INDEX reminders_dispatch_due ON reminders (send_at, id)
            WHERE status = 'pending' AND telegram_bot_id IS NOT NULL
                AND queued_generation IS DISTINCT FROM generation;
        CREATE TABLE telegram_outbox (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            business_id text NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            bot_id bigint NOT NULL,
            chat_id bigint NOT NULL,
            update_id bigint,
            reminder_id text REFERENCES reminders(id) ON DELETE CASCADE,
            reminder_generation bigint,
            message text,
            reply_markup jsonb,
            status text NOT NULL DEFAULT 'ready'
                CHECK (status IN ('ready','dispatching','sent','failed','canceled','ambiguous')),
            attempts integer NOT NULL DEFAULT 0,
            ready_at timestamptz NOT NULL DEFAULT now(),
            lease_until timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            last_error text,
            CHECK ((update_id IS NOT NULL AND reminder_id IS NULL AND reminder_generation IS NULL)
                OR (update_id IS NULL AND reminder_id IS NOT NULL AND reminder_generation IS NOT NULL)),
            FOREIGN KEY (bot_id, business_id)
                REFERENCES telegram_bots (bot_id, business_id) ON DELETE CASCADE,
            UNIQUE (bot_id, update_id),
            UNIQUE (reminder_id, reminder_generation)
        );
        CREATE INDEX telegram_outbox_ready ON telegram_outbox (ready_at, id)
            WHERE status = 'ready';
        CREATE INDEX telegram_outbox_chat ON telegram_outbox (bot_id, chat_id, id)
            WHERE status IN ('ready','dispatching');
        CREATE INDEX telegram_outbox_lease ON telegram_outbox (lease_until)
            WHERE status = 'dispatching';
        CREATE INDEX telegram_outbox_finished ON telegram_outbox (finished_at)
            WHERE status IN ('sent','failed','canceled','ambiguous');
        CREATE TABLE telegram_bot_delivery (
            bot_id bigint PRIMARY KEY REFERENCES telegram_bots(bot_id) ON DELETE CASCADE,
            available_at timestamptz NOT NULL DEFAULT now(),
            reply_streak integer NOT NULL DEFAULT 0
        );
        CREATE TABLE telegram_chat_delivery (
            bot_id bigint NOT NULL REFERENCES telegram_bots(bot_id) ON DELETE CASCADE,
            chat_id bigint NOT NULL,
            available_at timestamptz NOT NULL,
            PRIMARY KEY (bot_id, chat_id)
        );
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Keep the additive schema during rollback. Restore the matching database "
        "backup only after reconciling queued updates and deliveries."
    )
