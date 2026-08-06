from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import OperationLog
from yuxi.utils import logger


async def log_operation(
    db: AsyncSession,
    user_id: int | None,
    operation: str,
    details: str | None = None,
    request: Request | None = None,
) -> None:
    try:
        ip_address = request.client.host if request and request.client else None
        db.add(OperationLog(user_id=user_id, operation=operation, details=details, ip_address=ip_address))
        await db.commit()
    except Exception as exc:
        # 操作日志属于审计链路:当前调用方仍按历史约定不因日志故障回滚业务,
        # 但必须留下结构化异常,否则会出现“业务成功且审计静默丢失”的不可观测状态。
        logger.error(
            f"操作日志写入失败: operation={operation} user_id={user_id} "
            f"exception_type={type(exc).__name__}"
        )
