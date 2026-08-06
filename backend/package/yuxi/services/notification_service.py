from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import UserNotification


async def create_notification(
    *, recipient_uid: str, notification_type: str, title: str, message: str,
    idempotency_key: str, resource_type: str | None = None, resource_id: str | None = None,
) -> dict[str, Any]:
    async with pg_manager.get_async_session_context() as session:
        existing = await session.scalar(
            select(UserNotification).where(UserNotification.idempotency_key == idempotency_key)
        )
        if existing is None:
            existing = UserNotification(
                notification_id=uuid.uuid4().hex, recipient_uid=recipient_uid,
                notification_type=notification_type, title=title, message=message,
                resource_type=resource_type, resource_id=resource_id, idempotency_key=idempotency_key,
            )
            session.add(existing)
            await session.flush()
        return serialize_notification(existing)


async def list_notifications(*, recipient_uid: str, unread_only: bool, offset: int, limit: int) -> dict[str, Any]:
    async with pg_manager.get_async_session_context() as session:
        filters = [UserNotification.recipient_uid == recipient_uid]
        if unread_only:
            filters.append(UserNotification.read_at.is_(None))
        total = int(await session.scalar(select(func.count()).select_from(UserNotification).where(*filters)) or 0)
        result = await session.execute(
            select(UserNotification).where(*filters)
            .order_by(UserNotification.created_at.desc(), UserNotification.id.desc())
            .offset(offset).limit(limit)
        )
        items = [serialize_notification(item) for item in result.scalars().all()]
        return {"items": items, "total": total, "offset": offset, "limit": limit, "has_more": offset + len(items) < total}


async def mark_notification_read(*, notification_id: str, recipient_uid: str) -> bool:
    async with pg_manager.get_async_session_context() as session:
        result = await session.execute(
            update(UserNotification)
            .where(UserNotification.notification_id == notification_id, UserNotification.recipient_uid == recipient_uid)
            .values(read_at=datetime.now(UTC).replace(tzinfo=None))
        )
        return bool(result.rowcount)


def serialize_notification(item: UserNotification) -> dict[str, Any]:
    return {
        "notification_id": item.notification_id, "type": item.notification_type,
        "title": item.title, "message": item.message, "resource_type": item.resource_type,
        "resource_id": item.resource_id, "read_at": item.read_at.isoformat() if item.read_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
