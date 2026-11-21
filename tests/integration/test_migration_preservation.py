"""Exercise legacy upgrades in a disposable database."""

import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.channels.telegram_repository import TelegramRepository
from tests.integration.helpers import connect


@pytest.fixture
def migration_db(scheduling_db):
    parts = urlsplit(scheduling_db["url"])
    name = "capy_migration_" + uuid4().hex + "_test"
    url = urlunsplit(parts._replace(path="/" + name))
    admin = psycopg.connect(scheduling_db["url"], autocommit=True)
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    env = {**os.environ, "DATABASE_URL": url}

    def migrate(target, *, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", target],
            env=env,
            check=check,
            capture_output=True,
            text=True,
        )

    try:
        yield url, migrate
    finally:
        admin.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )
        admin.close()


def test_populated_legacy_database_is_preserved(migration_db):
    url, migrate = migration_db
    migrate("0002_telegram_channel")
    with connect(url) as conn:
        conn.execute(
            "INSERT INTO businesses (id,name,category,timezone) VALUES ('b','Legacy','wellness','UTC')"
        )
        conn.execute(
            "INSERT INTO services (id,business_id,name,duration_minutes,price_minor) VALUES ('s','b','Massage',60,100)"
        )
        conn.execute(
            "INSERT INTO professionals (id,business_id,name) VALUES ('p','b','Alex')"
        )
        conn.execute(
            "INSERT INTO customers (id,business_id,full_name,phone) VALUES ('c','b','Existing Customer','+5511999998888')"
        )
        conn.execute("""INSERT INTO bookings (id,business_id,service_id,professional_id,customer_id,starts_at,ends_at,
            occupied_from,occupied_until,status,token_digest) VALUES
            ('booking','b','s','p','c','2099-01-05T15:00Z','2099-01-05T16:00Z','2099-01-05T15:00Z','2099-01-05T16:00Z','scheduled','legacy-digest')""")
        conn.execute("""INSERT INTO telegram_identities (business_id,bot_id,telegram_user_id,customer_id,verified_phone,verified_name)
            VALUES ('b',42,101,'c','+5511999998888','Existing Customer')""")
    migrate("0006_telegram_publications")
    rejected = migrate("head", check=False)
    assert rejected.returncode != 0
    assert "Use a fresh database for the massage studio schema" in rejected.stderr
    with connect(url) as conn:
        assert (
            conn.execute(
                "SELECT customer_id FROM bookings WHERE id='booking'"
            ).fetchone()["customer_id"]
            == "c"
        )
        identity = conn.execute(
            "SELECT customer_id,contact_phone,display_name FROM telegram_identities"
        ).fetchone()
        assert identity == dict(
            customer_id="c",
            contact_phone="+5511999998888",
            display_name="Existing Customer",
        )
        conn.execute(
            "INSERT INTO customers (id,business_id,full_name,phone) VALUES ('different','b','Different','+5511999998888')"
        )
        conn.execute(
            "INSERT INTO customers (id,business_id,full_name) VALUES ('optional','b','Optional')"
        )


def test_telegram_customer_migration_preserves_ownership_and_pending_work(migration_db):
    url, migrate = migration_db
    migrate("0007_massage_studio")
    with connect(url) as conn:
        conn.execute("INSERT INTO businesses (id,name,category,timezone) VALUES ('b','Studio','wellness','UTC')")
        conn.execute("INSERT INTO massagistas (id,business_id,name) VALUES ('p','b','Alex')")
        conn.execute(
            "INSERT INTO services (id,business_id,service_type,duration_minutes,price_minor) "
            "VALUES ('s','b','Massage',60,100)"
        )
        conn.execute(
            "INSERT INTO customers (id,business_id,full_name,phone) VALUES "
            "('c','b','Existing Customer','+5511999998888'), "
            "('manual','b','Manual Customer','+5511999998888')"
        )
        conn.execute("""
            INSERT INTO bookings (id,business_id,service_id,professional_id,customer_id,
                starts_at,ends_at,occupied_from,occupied_until,status)
            VALUES ('booking','b','s','p','c','2099-01-05T15:00Z','2099-01-05T16:00Z',
                '2099-01-05T15:00Z','2099-01-05T16:00Z','scheduled')
        """)
        conn.execute("""
            INSERT INTO telegram_identities (
                business_id, bot_id, telegram_user_id, customer_id, username, contact_phone, display_name
            ) VALUES
                ('b',42,101,'c','existing','+5511999990000','@existing'),
                ('b',42,102,NULL,'unlinked','+5511999998888','@unlinked')
        """)
        conn.execute("INSERT INTO telegram_bots (bot_id,business_id,token_env) VALUES (42,'b','BOT_TOKEN')")
        conn.execute("""
            INSERT INTO telegram_updates (bot_id,update_id,business_id,status,reply_text,chat_id,telegram_user_id,payload)
            VALUES (42,1,'b','reply_ready','Legacy response',NULL,NULL,NULL),
                (42,2,'b','received',NULL,101,101,
                 '{"message":{"chat":{"id":101,"type":"private"},"from":{"id":101},"text":"Pending"}}')
        """)
        conn.execute("""
            INSERT INTO telegram_outbox (bot_id,update_id,business_id,chat_id,message)
            VALUES (42,3,'b',101,'Pending reply')
        """)

    rejected = migrate("head", check=False)
    assert rejected.returncode != 0
    assert "Resolve legacy reply_ready updates" in rejected.stderr
    with connect(url) as conn:
        assert conn.execute(
            "SELECT reply_text FROM telegram_updates WHERE update_id=1"
        ).fetchone() == {"reply_text": "Legacy response"}
        conn.execute("UPDATE telegram_updates SET status='delivered', reply_text=NULL WHERE update_id=1")

    migrate("0008_telegram_customers")
    with connect(url) as conn:
        conn.execute("""INSERT INTO telegram_publications (outgoing_id,due_at)
                        SELECT id, now() + interval '15 minutes' FROM telegram_outbox""")
        conn.execute("INSERT INTO telegram_bots (bot_id,business_id,token_env) VALUES (43,'b','OTHER_TOKEN')")
        conn.execute("""INSERT INTO telegram_outbox (bot_id,update_id,business_id,chat_id,message)
                        VALUES (43,4,'b',101,'Other bot reply')""")
    rejected = migrate("head", check=False)
    assert rejected.returncode != 0
    assert "Finish pending work for other Telegram bots" in rejected.stderr
    with connect(url) as conn:
        conn.execute("UPDATE telegram_outbox SET status='sent', message=NULL WHERE bot_id=43")
    migrate("head")
    with connect(url) as conn:
        assert conn.execute("SELECT customer_id FROM bookings").fetchone() == {"customer_id": "c"}
        rows = conn.execute("""
            SELECT id, full_name, phone, telegram_user_id, telegram_username, telegram_display_name
            FROM customers ORDER BY telegram_user_id NULLS LAST
        """).fetchall()
        assert rows[0] == {
            "id": "c", "full_name": "Existing Customer", "phone": "+5511999998888",
            "telegram_user_id": 101, "telegram_username": "existing", "telegram_display_name": "@existing",
        }
        assert rows[1]["id"] not in {"c", "manual"}
        assert rows[1]["telegram_user_id"] == 102
        assert rows[1]["phone"] == "+5511999998888"
        assert rows[2]["id"] == "manual" and rows[2]["telegram_user_id"] is None
        assert conn.execute(
            "SELECT status FROM telegram_updates WHERE update_id=2"
        ).fetchone() == {"status": "received"}
        assert conn.execute("""
            SELECT message, ready_at > now() + interval '14 minutes' AS delayed
            FROM telegram_outbox WHERE bot_id=42
        """).fetchone() == {"message": "Pending reply", "delayed": True}
        assert conn.execute(
            "SELECT chat_id FROM telegram_conversations"
        ).fetchone() == {"chat_id": 101}
        assert conn.execute("""
            SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename ~ '^telegram_'
            ORDER BY tablename
        """).fetchall() == [
            {"tablename": "telegram_conversations"}, {"tablename": "telegram_outbox"},
            {"tablename": "telegram_updates"},
        ]
        assert conn.execute("""
            SELECT to_regclass('telegram_identities') IS NULL
               AND to_regclass('telegram_poll_offsets') IS NULL AS removed
        """).fetchone() == {"removed": True}

    repo = TelegramRepository(url)
    assert repo.ensure_customer_for_sender("b", 42, 101, "Renamed")["customer_id"] == "c"
    assert repo.ensure_customer_for_sender("b", 42, 102, "Renamed")["customer_id"] == rows[1]["id"]
