from fastapi import APIRouter, Depends, Query

from yuxi.services.notification_service import list_notifications, mark_notification_read
from yuxi.storage.postgres.models_business import User
from server.utils.auth_middleware import get_required_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def get_notifications(
    unread_only: bool = Query(default=False), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
):
    return await list_notifications(recipient_uid=str(current_user.uid), unread_only=unread_only, offset=offset, limit=limit)


@router.post("/{notification_id}/read")
async def read_notification(notification_id: str, current_user: User = Depends(get_required_user)):
    return {"updated": await mark_notification_read(notification_id=notification_id, recipient_uid=str(current_user.uid))}
