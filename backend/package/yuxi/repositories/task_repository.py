from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, func, select, text, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TaskRecord


class TaskRepository:
    async def get_by_id(self, task_id: str) -> TaskRecord | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.id == task_id))
            return result.scalar_one_or_none()

    async def list(self, status: str | None = None, limit: int = 100) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            stmt = select(TaskRecord)
            if status:
                stmt = stmt.where(TaskRecord.status == status)
            stmt = stmt.order_by(TaskRecord.created_at.desc()).limit(max(limit, 0))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_all(self) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).order_by(TaskRecord.created_at.desc()))
            return list(result.scalars().all())

    async def list_for_startup(self, *, terminal_limit: int, terminal_statuses: set[str]) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            active_result = await session.execute(
                select(TaskRecord)
                .where(TaskRecord.status.not_in(terminal_statuses))
                .order_by(TaskRecord.created_at.desc())
            )
            terminal_result = await session.execute(
                select(TaskRecord)
                .where(TaskRecord.status.in_(terminal_statuses))
                .order_by(TaskRecord.created_at.desc())
                .limit(max(terminal_limit, 0))
            )
        return [*active_result.scalars().all(), *terminal_result.scalars().all()]

    async def get_list_summary(self, *, status: str | None) -> dict[str, Any]:
        async with pg_manager.get_async_session_context() as session:
            status_rows = await session.execute(select(TaskRecord.status, func.count()).group_by(TaskRecord.status))
            type_rows = await session.execute(select(TaskRecord.type, func.count()).group_by(TaskRecord.type))
        status_counts = {str(value): int(count) for value, count in status_rows}
        return {
            "total": sum(status_counts.values()),
            "filtered_total": status_counts.get(status, 0) if status else sum(status_counts.values()),
            "status_counts": status_counts,
            "type_counts": {str(value): int(count) for value, count in type_rows},
        }

    async def find_by_payload(
        self,
        *,
        task_type: str,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
    ) -> TaskRecord | None:
        async with pg_manager.get_async_session_context() as session:
            statement = select(TaskRecord).where(TaskRecord.type == task_type)
            if statuses is not None:
                statement = statement.where(TaskRecord.status.in_(statuses))
            result = await session.execute(statement.order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc()))
            for record in result.scalars():
                payload = record.payload if isinstance(record.payload, dict) else {}
                if all(payload.get(key) == value for key, value in payload_match.items()):
                    return record
        return None

    async def find_any_by_payload(
        self,
        *,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
    ) -> TaskRecord | None:
        async with pg_manager.get_async_session_context() as session:
            statement = select(TaskRecord)
            if statuses is not None:
                statement = statement.where(TaskRecord.status.in_(statuses))
            result = await session.execute(statement.order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc()))
            for record in result.scalars():
                payload = record.payload if isinstance(record.payload, dict) else {}
                if all(payload.get(key) == value for key, value in payload_match.items()):
                    return record
        return None

    async def delete_by_payload(
        self,
        *,
        payload_match: dict[str, Any],
        statuses: set[str],
    ) -> list[str]:
        if not payload_match:
            raise ValueError("Task payload match must not be empty")

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.status.in_(statuses)))
            matching_ids = [
                record.id
                for record in result.scalars()
                if isinstance(record.payload, dict)
                and all(record.payload.get(key) == value for key, value in payload_match.items())
            ]
            if matching_ids:
                await session.execute(delete(TaskRecord).where(TaskRecord.id.in_(matching_ids)))
            return matching_ids

    async def upsert(self, task_id: str, data: dict[str, Any]) -> TaskRecord:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.id == task_id))
            record = result.scalar_one_or_none()
            if record is None:
                record = TaskRecord(id=task_id, **data)
                session.add(record)
                return record
            for key, value in data.items():
                # A cancellation is irreversible for a task record.  Do not let
                # a worker with an older in-memory snapshot erase another API
                # instance's cancellation request while persisting progress.
                if key == "cancel_requested" and not value:
                    continue
                setattr(record, key, value)
            return record

    async def request_cancellation(self, task_id: str) -> TaskRecord | None:
        """Atomically flag a non-terminal task as cancelled by its requester."""
        async with pg_manager.get_async_session_context() as session:
            statement = (
                update(TaskRecord)
                .where(TaskRecord.id == task_id, TaskRecord.status.not_in(("success", "failed", "cancelled")))
                .values(cancel_requested=1)
                .returning(TaskRecord)
            )
            result = await session.execute(statement)
            return result.scalar_one_or_none()

    async def claim_retry(self, task_id: str, *, max_retries: int) -> TaskRecord | None:
        """Atomically reserve one retry for a failed resumable task."""
        async with pg_manager.get_async_session_context() as session:
            statement = (
                update(TaskRecord)
                .where(
                    TaskRecord.id == task_id,
                    TaskRecord.status == "failed",
                    TaskRecord.retryable.is_(True),
                    TaskRecord.retry_count < max_retries,
                )
                .values(
                    status="pending",
                    progress=0.0,
                    message="任务已重新排队",
                    error=None,
                    result=None,
                    retry_count=TaskRecord.retry_count + 1,
                    started_at=None,
                    completed_at=None,
                )
                .returning(TaskRecord)
            )
            result = await session.execute(statement)
            return result.scalar_one_or_none()

    async def create_or_get_by_payload(
        self,
        *,
        task_id: str,
        data: dict[str, Any],
        task_type: str,
        payload_match: dict[str, Any],
        statuses: set[str] | None,
    ) -> tuple[TaskRecord, bool]:
        """Atomically return a matching task or persist a new one.

        Task payloads do not have a fixed relational schema, so a PostgreSQL
        advisory transaction lock serializes only attempts for the same logical
        task identity before the JSON payload comparison and insert.
        """
        lock_key = json.dumps(
            {"task_type": task_type, "payload_match": payload_match},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            statement = select(TaskRecord).where(TaskRecord.type == task_type)
            if statuses is not None:
                statement = statement.where(TaskRecord.status.in_(statuses))
            result = await session.execute(statement.order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc()))
            for record in result.scalars():
                payload = record.payload if isinstance(record.payload, dict) else {}
                if all(payload.get(key) == value for key, value in payload_match.items()):
                    return record, False

            record = TaskRecord(id=task_id, **data)
            session.add(record)
            await session.flush()
            return record, True

    async def delete(self, task_id: str) -> bool:
        """Delete a task by id. Returns True if deleted, False if not found."""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(delete(TaskRecord).where(TaskRecord.id == task_id))
            return result.rowcount > 0

    async def delete_terminal(self, task_id: str, statuses: set[str]) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(TaskRecord).where(TaskRecord.id == task_id, TaskRecord.status.in_(statuses))
            )
            return result.rowcount > 0

    async def delete_all(self) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(TaskRecord))
