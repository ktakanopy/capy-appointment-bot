"""Runtime mappings for the PostgreSQL schema managed by Alembic."""

from datetime import datetime, time

from sqlalchemy import BigInteger, Boolean, CHAR, Computed, DateTime, Identity, Integer, SmallInteger, Text, Time, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BusinessRow(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    minimum_notice_minutes: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    booking_horizon_days: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ProfessionalRow(Base):
    __tablename__ = "massagistas"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class ServiceRow(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[str] = mapped_column(Text)
    service_type: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(
        Text, Computed("service_type || ' ' || duration_minutes::text || ' min'", persisted=True)
    )
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'BRL'"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class WeeklyAvailabilityRule(Base):
    __tablename__ = "weekly_availability_rules"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[str] = mapped_column(Text)
    professional_id: Mapped[str] = mapped_column(Text)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    opens_at: Mapped[time] = mapped_column(Time)
    closes_at: Mapped[time] = mapped_column(Time)


class BlockedPeriod(Base):
    __tablename__ = "blocked_periods"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[str] = mapped_column(Text)
    professional_id: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, server_default=text("''"))


class CustomerRow(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    telegram_bot_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(Text)
    telegram_display_name: Mapped[str | None] = mapped_column(Text)


class BookingRow(Base):
    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[str] = mapped_column(Text)
    service_id: Mapped[str] = mapped_column(Text)
    professional_id: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occupied_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occupied_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    business_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[str] = mapped_column(Text)
    result_code: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    business_id: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default=text("'received'"))
    payload: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now() + interval '7 days'"))
    message_date: Mapped[int | None] = mapped_column(BigInteger)
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class TelegramConversation(Base):
    __tablename__ = "telegram_conversations"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    business_id: Mapped[str] = mapped_column(Text)
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    active_update_id: Mapped[int | None] = mapped_column(BigInteger)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_date: Mapped[int | None] = mapped_column(BigInteger)
    last_update_id: Mapped[int | None] = mapped_column(BigInteger)
    checkpoint_thread: Mapped[str | None] = mapped_column(Text)


class TelegramOutboxRow(Base):
    __tablename__ = "telegram_outbox"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    business_id: Mapped[str] = mapped_column(Text)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    update_id: Mapped[int] = mapped_column(BigInteger)
    message: Mapped[str | None] = mapped_column(Text)
    reply_markup: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[str] = mapped_column(Text, server_default=text("'ready'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
