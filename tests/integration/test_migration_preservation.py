"""Exercise an upgrade with populated legacy rows in its own disposable database."""
import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
import psycopg
from psycopg import sql
from app.database import connect


def test_populated_legacy_database_preserves_booking_and_verified_link(scheduling_db):
    parts = urlsplit(scheduling_db["url"])
    name = "capy_migration_" + uuid4().hex + "_test"
    url = urlunsplit(parts._replace(path="/"+name))
    admin = psycopg.connect(scheduling_db["url"], autocommit=True)
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    env = {**os.environ, "DATABASE_URL": url}
    def migrate(target):
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", target], env=env, check=True, capture_output=True)
    try:
        migrate("0002_telegram_channel")
        with connect(url) as conn:
            conn.execute("INSERT INTO businesses (id,name,category,timezone) VALUES ('b','Legacy','wellness','UTC')")
            conn.execute("INSERT INTO services (id,business_id,name,duration_minutes,price_minor) VALUES ('s','b','Massage',60,100)")
            conn.execute("INSERT INTO professionals (id,business_id,name) VALUES ('p','b','Alex')")
            conn.execute("INSERT INTO customers (id,business_id,full_name,phone) VALUES ('c','b','Existing Customer','+5511999998888')")
            conn.execute("""INSERT INTO bookings (id,business_id,service_id,professional_id,customer_id,starts_at,ends_at,
                occupied_from,occupied_until,status,token_digest) VALUES
                ('booking','b','s','p','c','2099-01-05T15:00Z','2099-01-05T16:00Z','2099-01-05T15:00Z','2099-01-05T16:00Z','scheduled','legacy-digest')""")
            conn.execute("""INSERT INTO telegram_identities (business_id,bot_id,telegram_user_id,customer_id,verified_phone,verified_name)
                VALUES ('b',42,101,'c','+5511999998888','Existing Customer')""")
        migrate("head")
        with connect(url) as conn:
            assert conn.execute("SELECT customer_id FROM bookings WHERE id='booking'").fetchone()["customer_id"] == "c"
            identity = conn.execute("SELECT customer_id,contact_phone,display_name FROM telegram_identities").fetchone()
            assert identity == dict(customer_id="c",contact_phone="+5511999998888",display_name="Existing Customer")
            conn.execute("INSERT INTO customers (id,business_id,full_name,phone) VALUES ('different','b','Different','+5511999998888')")
            conn.execute("INSERT INTO customers (id,business_id,full_name) VALUES ('optional','b','Optional')")
    finally:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()
