"""Tasker 取消语义单元测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from yuxi.services import task_service
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
        active = [(task_id, data) for task_id, data in self.records.items() if data["status"] not in terminal_statuses]
        terminal = [(task_id, data) for task_id, data in self.records.items() if data["status"] in terminal_statuses]
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

    async def claim_retry(self, task_id, *, max_retries):
        data = self.records.get(task_id)
        if (
            data is None
            or data["status"] != "failed"
            or not data.get("retryable")
            or int(data.get("retry_count", 0)) >= max_retries
        ):
            return None
        data.update(
            status="pending", progress=0.0, message="任务已重新排队", error=None,
            result=None, retry_count=int(data.get("retry_count", 0)) + 1,
            started_at=None, completed_at=None,
        )
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

    async def find_any_by_payload(self, *, payload_match, statuses=None):
        for task_id, data in self.records.items():
            if statuses is not None and data["status"] not in statuses:
                continue
            if all(data.get("payload", {}).get(key) == value for key, value in payload_match.items()):
                return SimpleNamespace(to_dict=lambda: {"id": task_id, **data})
        return None

    async def delete_by_payload(self, *, payload_match, statuses):
        matching_ids = [
            task_id
            for task_id, data in self.records.items()
            if data["status"] in statuses
            and all(data.get("payload", {}).get(key) == value for key, value in payload_match.items())
        ]
        for task_id in matching_ids:
            del self.records[task_id]
        return matching_ids


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
async def test_cancelled_task_emits_cancelled_notification(monkeypatch):
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = FakeTaskRepository()
    notifications = []

    async def fake_create_notification(**kwargs):
        notifications.append(kwargs)

    from yuxi.services import notification_service

    monkeypatch.setattr(notification_service, "create_notification", fake_create_notification)
    task = await tasker.enqueue(
        name="cancelled notification",
        task_type="test",
        payload={"uid": "user-1"},
        coroutine=lambda context: asyncio.sleep(0),
    )

    await tasker._update_task(task.id, status="cancelled", message="用户主动取消")

    assert notifications == [
        {
            "recipient_uid": "user-1",
            "notification_type": "task_cancelled",
            "title": "任务已取消",
            "message": "用户主动取消",
            "resource_type": "task",
            "resource_id": task.id,
            "idempotency_key": f"task:{task.id}:cancelled",
        }
    ]


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
async def test_retry_task_requeues_resumable_failure_and_increments_count():
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = FakeTaskRepository()
    executed = asyncio.Event()

    async def handler(context):
        executed.set()
        return {"ok": True}

    tasker.register_resumable_handler("academic_paper_import", handler)
    task = await tasker.enqueue(
        name="paper", task_type="academic_paper_import", payload={"uid": "u1"}, coroutine=handler
    )
    await tasker._update_task(
        task.id, status="failed", error="temporary", retryable=True, dependency="semantic_scholar"
    )

    retried = await tasker.retry_task(task.id)
    assert retried is not None
    assert retried["status"] == "pending"
    assert retried["retry_count"] == 1
    assert retried["dependency"] == "semantic_scholar"

    await tasker.start()
    try:
        await asyncio.wait_for(executed.wait(), timeout=1)
    finally:
        await tasker.shutdown()


@pytest.mark.asyncio
async def test_retry_task_rejects_limit_and_non_resumable_failure():
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = FakeTaskRepository()
    task = await tasker.enqueue(name="plain", task_type="plain", coroutine=lambda context: asyncio.sleep(0))
    await tasker._update_task(task.id, status="failed", retryable=True, dependency="model")
    assert await tasker.retry_task(task.id) is None

    tasker.register_resumable_handler("plain", lambda context: asyncio.sleep(0))
    task.retry_count = 3
    tasker._tasks[task.id] = task
    tasker._repo.records[task.id]["retry_count"] = 3
    assert await tasker.retry_task(task.id, max_retries=3) is None


@pytest.mark.asyncio
async def test_retry_task_shared_repository_allows_only_one_concurrent_claim():
    repository = FakeTaskRepository()
    first = Tasker(worker_count=1, default_timeout_seconds=5)
    second = Tasker(worker_count=1, default_timeout_seconds=5)
    first._repo = repository
    second._repo = repository

    async def handler(context):
        return {"ok": True}

    first.register_resumable_handler("academic_paper_import", handler)
    second.register_resumable_handler("academic_paper_import", handler)
    task = await first.enqueue(
        name="paper", task_type="academic_paper_import", payload={}, coroutine=handler
    )
    await first._update_task(task.id, status="failed", retryable=True, dependency="semantic_scholar")
    second._tasks[task.id] = task_service.Task.from_dict({"id": task.id, **repository.records[task.id]})

    results = await asyncio.gather(first.retry_task(task.id), second.retry_task(task.id))
    assert sum(result is not None for result in results) == 1
    assert repository.records[task.id]["retry_count"] == 1


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


@pytest.mark.asyncio
async def test_find_active_task_by_payload_across_task_types():
    repository = FakeTaskRepository()
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = repository
    matching = await tasker.enqueue(
        name="analysis",
        task_type="academic_paper_analysis",
        payload={"kb_id": "kb-1"},
        coroutine=lambda context: asyncio.sleep(0),
    )
    await tasker.enqueue(
        name="other",
        task_type="research_synthesis",
        payload={"kb_id": "kb-2"},
        coroutine=lambda context: asyncio.sleep(0),
    )

    found = await tasker.find_task_by_payload_any_type(
        payload_match={"kb_id": "kb-1"},
        statuses={"pending", "running"},
    )

    assert found is not None
    assert found.id == matching.id


@pytest.mark.asyncio
async def test_delete_terminal_tasks_by_payload_preserves_active_work_and_releases_memory():
    repository = FakeTaskRepository()
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = repository
    completed = await tasker.enqueue(
        name="completed",
        task_type="research_synthesis",
        payload={"kb_id": "kb-1"},
        coroutine=lambda context: asyncio.sleep(0),
    )
    active = await tasker.enqueue(
        name="active",
        task_type="academic_paper_analysis",
        payload={"kb_id": "kb-1"},
        coroutine=lambda context: asyncio.sleep(0),
    )
    await tasker._update_task(completed.id, status="success")

    deleted = await tasker.delete_terminal_tasks_by_payload(payload_match={"kb_id": "kb-1"})

    assert deleted == 1
    assert completed.id not in repository.records
    assert completed.id not in tasker._tasks
    assert active.id in repository.records
    assert active.id in tasker._tasks


@pytest.mark.asyncio
async def test_unknown_task_failure_does_not_persist_or_log_provider_secrets(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    log_entries = []

    class FakeLogger:
        def __getattr__(self, level):
            def record(*args, **kwargs):
                log_entries.append((level, args, kwargs))

            return record

    async def fail(context):
        raise RuntimeError(secret)

    monkeypatch.setattr(task_service, "logger", FakeLogger())
    tasker = Tasker(worker_count=1, default_timeout_seconds=5)
    tasker._repo = FakeTaskRepository()

    await tasker.start()
    try:
        task = await tasker.enqueue(name="provider failure", task_type="test", coroutine=fail)
        failed = await _wait_for_status(tasker, task.id, "failed")
    finally:
        await tasker.shutdown()

    assert failed["error"] == "任务执行失败，请稍后重试"
    assert secret not in repr(log_entries)
