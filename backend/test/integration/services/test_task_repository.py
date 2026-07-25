"""TaskRepository 的 PostgreSQL 并发幂等测试。"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select

from yuxi.repositories.task_repository import TaskRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TaskRecord


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _task_data(payload: dict[str, str]) -> dict:
    return {
        "name": "pytest unique task",
        "type": "pytest_unique_task",
        "status": "pending",
        "progress": 0.0,
        "message": "",
        "payload": payload,
        "result": None,
        "error": None,
        "cancel_requested": 0,
    }


async def test_create_or_get_by_payload_is_atomic_across_database_transactions():
    task_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    payload = {"run_id": f"pytest-{uuid.uuid4().hex}"}
    repository = TaskRepository()

    async def create(task_id: str):
        return await repository.create_or_get_by_payload(
            task_id=task_id,
            data=_task_data(payload),
            task_type="pytest_unique_task",
            payload_match=payload,
            statuses={"pending", "running"},
        )

    try:
        results = await asyncio.wait_for(asyncio.gather(*(create(task_id) for task_id in task_ids)), timeout=10)

        assert sorted(created for _, created in results) == [False, True]
        created_record = next(record for record, created in results if created)
        assert {record.id for record, _ in results} == {created_record.id}

        async with pg_manager.get_async_session_context() as session:
            count = await session.scalar(
                select(func.count()).select_from(TaskRecord).where(TaskRecord.id.in_(task_ids))
            )
        assert count == 1
    finally:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(TaskRecord).where(TaskRecord.id.in_(task_ids)))


async def test_get_list_summary_aggregates_persisted_task_records():
    task_ids = [uuid.uuid4().hex for _ in range(3)]
    repository = TaskRepository()
    task_type = f"pytest_summary_{uuid.uuid4().hex}"

    try:
        for index, task_id in enumerate(task_ids):
            data = _task_data({"summary_test": task_id})
            data["type"] = task_type
            data["status"] = "pending" if index < 2 else "success"
            await repository.upsert(task_id, data)

        all_summary = await repository.get_list_summary(status=None)
        pending_summary = await repository.get_list_summary(status="pending")

        assert all_summary["total"] == sum(all_summary["status_counts"].values())
        assert all_summary["type_counts"][task_type] == 3
        assert pending_summary["filtered_total"] == pending_summary["status_counts"].get("pending", 0)
        assert pending_summary["filtered_total"] >= 2
    finally:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(TaskRecord).where(TaskRecord.id.in_(task_ids)))
