"""Tasker 取消语义单元测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from yuxi.services.task_service import Tasker


class FakeTaskRepository:
    def __init__(self):
        self.records = {}

    async def list_all(self):
        return [
            SimpleNamespace(to_dict=lambda data=data, task_id=task_id: {"id": task_id, **data})
            for task_id, data in self.records.items()
        ]

    async def list_for_startup(self, *, terminal_limit, terminal_statuses):
        active = [
            (task_id, data) for task_id, data in self.records.items() if data["status"] not in terminal_statuses
        ]
        terminal = [
            (task_id, data) for task_id, data in self.records.items() if data["status"] in terminal_statuses
        ]
        terminal.sort(key=lambda item: str(item[1].get("created_at") or ""), reverse=True)
        return [
            SimpleNamespace(to_dict=lambda data=data, task_id=task_id: {"id": task_id, **data})
            for task_id, data in [*active, *terminal[:terminal_limit]]
        ]

    async def get_by_id(self, task_id):
        data = self.records.get(task_id)
        return SimpleNamespace(to_dict=lambda: {"id": task_id, **data}) if data else None

    async def upsert(self, task_id, data):
        self.records[task_id] = data

    async def create_or_get_by_payload(self, *, task_id, data, task_type, payload_match, statuses):
        for existing_id, existing_data in self.records.items():
            if existing_data["type"] != task_type:
                continue
            if statuses is not None and existing_data["status"] not in statuses:
                continue
            payload = existing_data.get("payload") or {}
            if all(payload.get(key) == value for key, value in payload_match.items()):
                return SimpleNamespace(to_dict=lambda: {"id": existing_id, **existing_data}), False
        self.records[task_id] = data
        return SimpleNamespace(to_dict=lambda: {"id": task_id, **data}), True

    async def delete(self, task_id):
        self.records.pop(task_id, None)
        return True

    async def request_cancellation(self, task_id):
        data = self.records.get(task_id)
        if data is None or data["status"] in {"success", "failed", "cancelled"}:
            return None
        data["cancel_requested"] = 1
        return SimpleNamespace(to_dict=lambda: {"id": task_id, **data})

    async def delete_terminal(self, task_id, statuses):
        data = self.records.get(task_id)
        if data is None or data["status"] not in statuses:
            return False
        del self.records[task_id]
        return True

    async def find_by_payload(self, *, task_type, payload_match, statuses=None):
        for task_id, data in self.records.items():
            if data["type"] != task_type or (statuses is not None and data["status"] not in statuses):
                continue
            if all(data.get("payload", {}).get(key) == value for key, value in payload_match.items()):
                return SimpleNamespace(to_dict=lambda: {"id": task_id, **data})
        return None


async def _wait_for_status(tasker, task_id, expected_status):
    for _ in range(100):
        task = await tasker.get_task(task_id)
        if task and task["status"] == expected_status:
            return task
        await asyncio.sleep(0.01)
    pytest.fail(f"task {task_id} did not reach {expected_status}")


@pytest.mark.asyncio
async def test_non_cooperative_task_is_not_reported_as_cancelled_after_completion():
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = FakeTaskRepository()
    started = asyncio.Event()
    release = asyncio.Event()

    async def coroutine(context):
        started.set()
        await release.wait()
        return {"completed": True}

    await tasker.start()
    try:
        task = await tasker.enqueue(name="test", task_type="test", coroutine=coroutine)
        await started.wait()
        assert await tasker.cancel_task(task.id) is True
        release.set()

        completed = await _wait_for_status(tasker, task.id, "success")
        assert completed["cancel_requested"] is True
        assert completed["message"] == "任务已完成（取消请求在安全检查点后到达）"
    finally:
        await tasker.shutdown()


@pytest.mark.asyncio
async def test_cooperative_task_is_cancelled_at_explicit_safety_point():
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = FakeTaskRepository()
    started = asyncio.Event()
    release = asyncio.Event()

    async def coroutine(context):
        started.set()
        await release.wait()
        await context.raise_if_cancelled()

    await tasker.start()
    try:
        task = await tasker.enqueue(name="test", task_type="test", coroutine=coroutine)
        await started.wait()
        assert await tasker.cancel_task(task.id) is True
        release.set()

        cancelled = await _wait_for_status(tasker, task.id, "cancelled")
        assert cancelled["cancel_requested"] is True
    finally:
        await tasker.shutdown()


@pytest.mark.asyncio
async def test_unique_payload_is_shared_across_tasker_instances():
    repository = FakeTaskRepository()
    first_tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    second_tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    first_tasker._repo = repository
    second_tasker._repo = repository

    async def coroutine(context):
        return None

    first, first_created = await first_tasker.enqueue_unique_by_payload(
        name="first",
        task_type="research_synthesis",
        payload={"run_id": "run-1"},
        payload_match={"run_id": "run-1"},
        statuses={"pending", "running"},
        coroutine=coroutine,
    )
    second, second_created = await second_tasker.enqueue_unique_by_payload(
        name="second",
        task_type="research_synthesis",
        payload={"run_id": "run-1"},
        payload_match={"run_id": "run-1"},
        statuses={"pending", "running"},
        coroutine=coroutine,
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert first_tasker._queue.qsize() == 1
    assert second_tasker._queue.qsize() == 0


@pytest.mark.asyncio
async def test_cancellation_from_another_tasker_reaches_the_execution_safety_point():
    repository = FakeTaskRepository()
    executor = Tasker(worker_count=1, default_timeout_seconds=5)
    requester = Tasker(worker_count=1, default_timeout_seconds=5)
    executor._repo = repository
    requester._repo = repository
    started = asyncio.Event()
    continue_execution = asyncio.Event()

    async def coroutine(context):
        started.set()
        await continue_execution.wait()
        await context.raise_if_cancelled()

    await executor.start()
    try:
        task = await executor.enqueue(name="cross-instance cancel", task_type="test", coroutine=coroutine)
        await started.wait()

        assert await requester.cancel_task(task.id) is True
        continue_execution.set()

        cancelled = await _wait_for_status(executor, task.id, "cancelled")
        assert cancelled["cancel_requested"] is True
    finally:
        await executor.shutdown()


@pytest.mark.asyncio
async def test_task_details_use_the_persisted_record_instead_of_a_stale_local_copy():
    repository = FakeTaskRepository()
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = repository
    task = await tasker.enqueue(name="stale cache", task_type="test", coroutine=lambda context: asyncio.sleep(0))

    repository.records[task.id]["status"] = "failed"
    repository.records[task.id]["message"] = "另一个实例已标记失败"

    detail = await tasker.get_task(task.id)
    assert detail["status"] == "failed"
    assert detail["message"] == "另一个实例已标记失败"
