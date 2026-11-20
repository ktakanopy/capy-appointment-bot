from __future__ import annotations

from datetime import time

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import load_settings
from app.database import database_session
from app.db_models import BusinessRow, ProfessionalRow, ServiceRow, WeeklyAvailabilityRule
from app.observability import debug_event


def _insert_seed_row(session: Session, model, values: dict) -> None:
    session.execute(insert(model).values(**values).on_conflict_do_nothing())


def seed() -> None:
    settings = load_settings()
    business_id = settings.demo_business_id
    professional_id = "professional-demo-ana"
    with database_session(settings.database_url) as session:
        _insert_seed_row(session, BusinessRow, {
            "id": business_id, "name": "Capy Massage Studio", "category": "Massage",
            "timezone": "America/Sao_Paulo", "minimum_notice_minutes": 5,
            "booking_horizon_days": 60,
        })
        _insert_seed_row(session, ProfessionalRow, {
            "id": professional_id, "business_id": business_id, "name": "Ana Costa", "active": True,
        })
        for service_id, service_type, duration, price in (
            ("service-shiatsu-30", "Shiatsu", 30, 10000),
            ("service-demo", "Shiatsu", 60, 18000),
            ("service-miofascial-60", "Liberação miofascial", 60, 18000),
        ):
            _insert_seed_row(session, ServiceRow, {
                "id": service_id, "business_id": business_id, "service_type": service_type,
                "description": service_type, "duration_minutes": duration,
                "buffer_before_minutes": 0, "buffer_after_minutes": 15,
                "price_minor": price, "currency": "BRL", "active": True,
            })
        for weekday in range(6):
            _insert_seed_row(session, WeeklyAvailabilityRule, {
                "id": f"hours-{professional_id}-{weekday}", "business_id": business_id,
                "professional_id": professional_id, "weekday": weekday,
                "opens_at": time(9), "closes_at": time(17),
            })
    debug_event("postgres.demo_seed.completed")


if __name__ == "__main__":
    seed()
