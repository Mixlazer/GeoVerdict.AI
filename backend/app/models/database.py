from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, JSON, Integer, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


class Base(DeclarativeBase):
    pass


class AnalysisRecord(Base):
    __tablename__ = "analysis_requests"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    city: Mapped[str] = mapped_column(String(120))
    business_type: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_building_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected_building_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_building_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SessionRecord(Base):
    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FeedbackRecord(Base):
    __tablename__ = "feedback_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    rating: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class LoginAttemptRecord(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(40), index=True)
    success: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await _migrate(connection)


async def _migrate(connection) -> None:
    result = await connection.execute(text("PRAGMA table_info(analysis_requests)"))
    columns = {row[1] for row in result.fetchall()}
    statements: list[str] = []
    if "user_id" not in columns:
        statements.append("ALTER TABLE analysis_requests ADD COLUMN user_id INTEGER")
    if "selected_building_name" not in columns:
        statements.append(
            "ALTER TABLE analysis_requests ADD COLUMN selected_building_name VARCHAR(255)"
        )
    if "selected_building_address" not in columns:
        statements.append(
            "ALTER TABLE analysis_requests ADD COLUMN selected_building_address TEXT"
        )
    if "selected_building_type" not in columns:
        statements.append(
            "ALTER TABLE analysis_requests ADD COLUMN selected_building_type VARCHAR(120)"
        )
    for statement in statements:
        await connection.execute(text(statement))
