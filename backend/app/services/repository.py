from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.database import (
    AnalysisRecord,
    FeedbackRecord,
    LoginAttemptRecord,
    SessionLocal,
    SessionRecord,
    UserRecord,
)


class AnalysisRepository:
    async def create(
        self,
        request_id: str,
        city: str,
        business_type: str,
        lat: float,
        lng: float,
        status: str,
        result_payload: dict | None = None,
        user_id: int | None = None,
        selected_building_name: str | None = None,
        selected_building_address: str | None = None,
        selected_building_type: str | None = None,
    ) -> AnalysisRecord:
        async with SessionLocal() as session:
            record = AnalysisRecord(
                id=request_id,
                city=city,
                business_type=business_type,
                lat=lat,
                lng=lng,
                user_id=user_id,
                selected_building_name=selected_building_name,
                selected_building_address=selected_building_address,
                selected_building_type=selected_building_type,
                status=status,
                result_payload=result_payload,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get(self, request_id: str) -> AnalysisRecord | None:
        async with SessionLocal() as session:
            return await session.get(AnalysisRecord, request_id)

    async def list_recent(self, limit: int = 100) -> Sequence[AnalysisRecord]:
        async with SessionLocal() as session:
            result = await session.execute(
                select(AnalysisRecord).order_by(AnalysisRecord.created_at.desc()).limit(limit)
            )
            return result.scalars().all()

    async def list_recent_by_user(self, user_id: int, limit: int = 100) -> Sequence[AnalysisRecord]:
        async with SessionLocal() as session:
            result = await session.execute(
                select(AnalysisRecord)
                .where(AnalysisRecord.user_id == user_id)
                .order_by(AnalysisRecord.created_at.desc())
                .limit(limit)
            )
            return result.scalars().all()

    async def update(
        self,
        request_id: str,
        *,
        status: str | None = None,
        result_payload: dict | None = None,
        error_message: str | None = None,
    ) -> AnalysisRecord | None:
        async with SessionLocal() as session:
            record = await session.get(AnalysisRecord, request_id)
            if record is None:
                return None
            if status is not None:
                record.status = status
            if result_payload is not None:
                record.result_payload = result_payload
            if error_message is not None:
                record.error_message = error_message
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(record)
            return record

    async def create_user(
        self, username: str, password_hash: str, full_name: str | None = None
    ) -> UserRecord | None:
        async with SessionLocal() as session:
            user = UserRecord(
                username=username,
                password_hash=password_hash,
                full_name=full_name,
            )
            session.add(user)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            await session.refresh(user)
            return user

    async def get_user(self, user_id: int) -> UserRecord | None:
        async with SessionLocal() as session:
            return await session.get(UserRecord, user_id)

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        async with SessionLocal() as session:
            result = await session.execute(select(UserRecord).where(UserRecord.username == username))
            return result.scalar_one_or_none()

    async def create_session(self, user_id: int, token: str) -> SessionRecord:
        async with SessionLocal() as session:
            session_record = SessionRecord(token=token, user_id=user_id)
            session.add(session_record)
            await session.commit()
            await session.refresh(session_record)
            return session_record

    async def get_session(self, token: str) -> SessionRecord | None:
        async with SessionLocal() as session:
            return await session.get(SessionRecord, token)

    async def create_feedback(
        self, message: str, rating: int, user_id: int | None = None, request_id: str | None = None
    ) -> FeedbackRecord:
        async with SessionLocal() as session:
            feedback = FeedbackRecord(
                user_id=user_id,
                request_id=request_id,
                message=message,
                rating=rating,
            )
            session.add(feedback)
            await session.commit()
            await session.refresh(feedback)
            return feedback

    async def record_login_attempt(self, username: str, success: bool) -> LoginAttemptRecord:
        async with SessionLocal() as session:
            attempt = LoginAttemptRecord(username=username, success=1 if success else 0)
            session.add(attempt)
            await session.commit()
            await session.refresh(attempt)
            return attempt

    async def list_failed_login_attempts_since(
        self,
        username: str,
        since: datetime,
    ) -> Sequence[LoginAttemptRecord]:
        async with SessionLocal() as session:
            result = await session.execute(
                select(LoginAttemptRecord)
                .where(
                    LoginAttemptRecord.username == username,
                    LoginAttemptRecord.success == 0,
                    LoginAttemptRecord.created_at >= since,
                )
                .order_by(LoginAttemptRecord.created_at.desc())
            )
            return result.scalars().all()

    async def list_feedback(self, limit: int = 100) -> Sequence[FeedbackRecord]:
        async with SessionLocal() as session:
            result = await session.execute(
                select(FeedbackRecord).order_by(FeedbackRecord.created_at.desc()).limit(limit)
            )
            return result.scalars().all()
