from __future__ import annotations

import argparse

from app.config import load_settings
from app.database import connect


def _seed_business(
    connection,
    *,
    business_id: str,
    name: str,
    category: str,
    timezone_name: str,
    suffix: str,
    service_name: str,
    service_description: str,
    duration_minutes: int,
    buffer_after_minutes: int,
    price_minor: int,
    professionals: list[tuple[str, str]],
):
    connection.execute(
        """INSERT INTO businesses (id,name,category,timezone,minimum_notice_minutes,booking_horizon_days,reminder_lead_minutes)
           VALUES (%s,%s,%s,%s,5,60,5)
           ON CONFLICT (id) DO UPDATE SET name=excluded.name, category=excluded.category, timezone=excluded.timezone,
             minimum_notice_minutes=excluded.minimum_notice_minutes, booking_horizon_days=excluded.booking_horizon_days,
             reminder_lead_minutes=excluded.reminder_lead_minutes""",
        (business_id, name, category, timezone_name),
    )
    service_id = f"service-{suffix}"
    connection.execute(
        """INSERT INTO services (id,business_id,name,description,duration_minutes,buffer_before_minutes,buffer_after_minutes,price_minor,currency)
           VALUES (%s,%s,%s,%s,%s,0,%s,%s,'BRL')
           ON CONFLICT (id) DO UPDATE SET business_id=excluded.business_id, name=excluded.name,
             description=excluded.description, duration_minutes=excluded.duration_minutes,
             buffer_before_minutes=excluded.buffer_before_minutes, buffer_after_minutes=excluded.buffer_after_minutes,
             price_minor=excluded.price_minor, currency=excluded.currency, active=true""",
        (service_id, business_id, service_name, service_description, duration_minutes, buffer_after_minutes, price_minor),
    )
    for professional_id, professional_name in professionals:
        connection.execute(
            """INSERT INTO professionals (id,business_id,name,active) VALUES (%s,%s,%s,true)
               ON CONFLICT (id) DO UPDATE SET business_id=excluded.business_id,name=excluded.name,active=true""",
            (professional_id, business_id, professional_name),
        )
        connection.execute(
            """INSERT INTO professional_services (business_id,professional_id,service_id) VALUES (%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (business_id, professional_id, service_id),
        )
        for weekday in range(6):
            connection.execute(
                """INSERT INTO weekly_availability_rules (id,business_id,professional_id,weekday,opens_at,closes_at)
                   VALUES (%s,%s,%s,%s,'09:00','17:00') ON CONFLICT (id) DO UPDATE SET
                   opens_at=excluded.opens_at,closes_at=excluded.closes_at""",
                (f"hours-{professional_id}-{weekday}", business_id, professional_id, weekday),
            )


def seed(include_second_business: bool = False) -> None:
    settings = load_settings()
    with connect(settings.database_url) as connection:
        _seed_business(
            connection,
            business_id=settings.demo_business_id,
            name="Capy Wellness Studio",
            category="Wellness studio",
            timezone_name="America/Sao_Paulo",
            suffix="demo",
            service_name="Relaxation massage",
            service_description="A full-body relaxation massage.",
            duration_minutes=60,
            buffer_after_minutes=15,
            price_minor=18000,
            professionals=[("professional-demo-ana", "Ana Costa"), ("professional-demo-rui", "Rui Mendes")],
        )
        if include_second_business:
            _seed_business(
                connection,
                business_id="second-demo-studio",
                name="Northside Bike Repair",
                category="Bicycle repair",
                timezone_name="America/Sao_Paulo",
                suffix="second",
                service_name="Bike tune-up",
                service_description="A standard safety and drivetrain tune-up.",
                duration_minutes=45,
                buffer_after_minutes=15,
                price_minor=12000,
                professionals=[("professional-second-alex", "Alex Ferreira"), ("professional-second-sam", "Sam Rocha")],
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load synthetic scheduling demo data")
    parser.add_argument("--include-second-business", action="store_true")
    args = parser.parse_args()
    seed(include_second_business=args.include_second_business)
