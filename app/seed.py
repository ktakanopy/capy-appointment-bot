from __future__ import annotations

import argparse
from datetime import time

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import load_settings
from app.database import database_session
from app.db_models import BusinessRow, ProfessionalRow, ProfessionalService, ServiceRow, WeeklyAvailabilityRule
from app.observability import debug_event


def _seed_business(
    session: Session,
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
    _upsert_seed_row(session, BusinessRow, {
        "id": business_id, "name": name, "category": category, "timezone": timezone_name,
        "minimum_notice_minutes": 5, "booking_horizon_days": 60, "reminder_lead_minutes": 5,
    })
    service_id = f"service-{suffix}"
    _upsert_seed_row(session, ServiceRow, {
        "id": service_id, "business_id": business_id, "name": service_name,
        "description": service_description, "duration_minutes": duration_minutes,
        "buffer_before_minutes": 0, "buffer_after_minutes": buffer_after_minutes,
        "price_minor": price_minor, "currency": "BRL", "active": True,
    })
    for professional_id, professional_name in professionals:
        _upsert_seed_row(session, ProfessionalRow, {
            "id": professional_id, "business_id": business_id, "name": professional_name, "active": True,
        })
        session.execute(insert(ProfessionalService).values(
            business_id=business_id, professional_id=professional_id, service_id=service_id,
        ).on_conflict_do_nothing())
        for weekday in range(6):
            _upsert_seed_row(session, WeeklyAvailabilityRule, {
                "id": f"hours-{professional_id}-{weekday}", "business_id": business_id,
                "professional_id": professional_id, "weekday": weekday,
                "opens_at": time(9), "closes_at": time(17),
            }, update_fields=("opens_at", "closes_at"))


def _upsert_seed_row(session: Session, model, values: dict, *, update_fields=None) -> None:
    row = insert(model).values(**values)
    fields = update_fields if update_fields is not None else tuple(name for name in values if name != "id")
    session.execute(row.on_conflict_do_update(
        index_elements=["id"], set_={name: row.excluded[name] for name in fields},
    ))


def seed(include_second_business: bool = False) -> None:
    settings = load_settings()
    with database_session(settings.database_url) as session:
        _seed_business(
            session,
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
            professionals=[
                ("professional-demo-ana", "Ana Costa"),
                ("professional-demo-rui", "Rui Mendes"),
            ],
        )
        if include_second_business:
            _seed_business(
                session,
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
                professionals=[
                    ("professional-second-alex", "Alex Ferreira"),
                    ("professional-second-sam", "Sam Rocha"),
                ],
            )

    debug_event("postgres.demo_seed.completed", businesses=2 if include_second_business else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load synthetic scheduling demo data")
    parser.add_argument("--include-second-business", action="store_true")
    args = parser.parse_args()
    seed(include_second_business=args.include_second_business)
