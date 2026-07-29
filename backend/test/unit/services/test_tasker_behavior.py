"""Tasker 行为单元测试：执行、关闭、终态保留与重启恢复。

使用内存 fake repo，不依赖真实数据库与 Docker。
"""

import asyncio

from yuxi.services import task_service
from yuxi.services.task_service import Tasker


class FakeRecord:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return self._data


class FakeRepo:
    """记录 upsert/delete 调用的内存仓库替身。"""

    def __init__(self, preset: list[FakeRecord] | None = None):
        self.preset = preset or []
        self.records = {record.to_dict()["id"]: record.to_dict().copy() for record in self.preset}
        self.upsert_calls = 0
        self.progress_writes: list[float] = []
        self.deleted: list[str] = []

    async def upsert(self, task_id: str, data: dict) -> None:
        self.upsert_calls += 1
        self.progress_writes.append(data.get("progress"))
        existing = self.records.get(task_id, {})
        if existing.get("cancel_requested") and not data.get("cancel_requested"):
            data = {**data, "cancel_requested": 1}
        self.records[task_id] = {"id": task_id, **data}

    async def delete(self, task_id: str) -> bool:
        self.deleted.append(task_id)
        self.records.pop(task_id, None)
        return True

    async def list_all(self) -> list[FakeRecord]:
        return [FakeRecord(data) for data in self.records.values()]

    async def list_for_startup(self, *, terminal_limit: int, terminal_statuses: set[str]) -> list[FakeRecord]:
        active = [data for data in self.records.values() if data.get("status") not in terminal_statuses]
        terminal = [data for data in self.records.values() if data.get("status") in terminal_statuses]
        terminal.sort(key=lambda data: str(data.get("created_at") or ""), reverse=True)
        return [FakeRecord(data) for data in [*active, *terminal[:terminal_limit]]]

    async def list(self, status: str | None = None, limit: int = 100) -> list[FakeRecord]:
        records = [data for data in self.records.values() if status is None or data.get("status") == status]
        records.sort(key=lambda data: str(data.get("created_at") or ""), reverse=True)
        return [FakeRecord(data) for data in records[:limit]]

    async def get_list_summary(self, *, status: str | None) -> dict:
        status_counts = {}
        type_counts = {}
        for data in self.records.values():
            status_counts[data["status"]] = status_counts.get(data["status"], 0) + 1
            type_counts[data["type"]] = type_counts.get(data["type"], 0) + 1
        return {
            "total": len(self.records),
            "filtered_total": status_counts.get(status, 0) if status else len(self.records),
            "status_counts": status_counts,
            "type_counts": type_counts,
        }

    async def get_by_id(self, task_id: str) -> FakeRecord | None:
        data = self.records.get(task_id)
        return FakeRecord(data) if data else None

    async def request_cancellation(self, task_id: str) -> FakeRecord | None:
        data = self.records.get(task_id)
        if data is None or data.get("status") in {"success", "failed", "cancelled"}:
            return None
        data["cancel_requested"] = 1
        return FakeRecord(data)

    async def delete_terminal(self, task_id: str, statuses: set[str]) -> bool:
        data = self.records.get(task_id)
        if data is None or data.get("status") not in statuses:
            return False
        self.deleted.append(task_id)
        del self.records[task_id]
        return True

    async def find_by_payload(self, *, task_type: str, payload_match: dict, statuses: set[str] | None = None):
        for data in self.records.values():
            if data.get("type") != task_type or (statuses is not None and data.get("status") not in statuses):
                continue
            if all((data.get("payload") or {}).get(key) == value for key, value in payload_match.items()):
                return FakeRecord(data)
        return None


async def _make_tasker(
    repo: FakeRepo,
    worker_count: int = 1,
    default_timeout_seconds: float = 60,
) -> Tasker:
    tasker = Tasker(worker_count=worker_count, default_timeout_seconds=default_timeout_seconds)
    tasker._repo = repo
    await tasker.start()
    return tasker


async def _wait_status(tasker: Tasker, task_id: str, statuses: set[str], timeout: float = 2.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        task = await tasker.get_task(task_id)
        if task and task["status"] in statuses:
            return task
        if loop.time() > deadline:
            raise AssertionError(f"任务 {task_id} 未在超时内进入 {statuses}")
        await asyncio.sleep(0.01)


async def test_task_context_exposes_payload():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)
    seen: dict = {}

    async def coro(ctx):
        seen["payload"] = ctx.payload
        return "ok"

    task = await tasker.enqueue(name="x", task_type="demo", payload={"a": 1}, coroutine=coro)
    await _wait_status(tasker, task.id, {"success"})

    assert seen["payload"] == {"a": 1}
    await tasker.shutdown()


async def test_progress_updates_are_throttled():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)

    async def coro(ctx):
        for percent in range(101):
            await ctx.set_progress(percent)
        return "done"

    task = await tasker.enqueue(name="x", task_type="demo", coroutine=coro)
    final = await _wait_status(tasker, task.id, {"success"})

    # 101 次进度推进经节流后落库次数应远小于 101（含 enqueue/running/success 也仅个位数额外写入）
    assert repo.upsert_calls < 60
    # 内存中进度仍为完整的 100
    assert final["progress"] == 100
    await tasker.shutdown()


async def test_explicit_none_result_is_persisted():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)

    async def coro(ctx):
        await ctx.set_result("partial")
        return None

    task = await tasker.enqueue(name="x", task_type="demo", coroutine=coro)
    final = await _wait_status(tasker, task.id, {"success"})

    # 协程最终返回 None 应覆盖中途结果（sentinel 区分「未传」与「显式 None」）
    assert final["result"] is None
    await tasker.shutdown()


async def test_shutdown_cancels_running_task_without_starting_queued_task():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)
    running = asyncio.Event()
    queued_started = asyncio.Event()

    async def blocking_coro(ctx):
        running.set()
        await asyncio.Event().wait()

    async def queued_coro(ctx):
        queued_started.set()

    active = await tasker.enqueue(name="active", task_type="demo", coroutine=blocking_coro)
    queued = await tasker.enqueue(name="queued", task_type="demo", coroutine=queued_coro)
    await running.wait()
    await _wait_status(tasker, active.id, {"running"})

    await asyncio.wait_for(tasker.shutdown(), timeout=1.0)

    assert (await tasker.get_task(active.id))["status"] == "cancelled"
    assert (await tasker.get_task(queued.id))["status"] == "pending"
    assert not queued_started.is_set()
    assert tasker._workers == []
    assert tasker._started is False


async def test_shutdown_exits_when_cancel_status_persistence_fails():
    class FailingCancelledRepo(FakeRepo):
        async def upsert(self, task_id: str, data: dict) -> None:
            await super().upsert(task_id, data)
            if data.get("status") == "cancelled":
                raise RuntimeError("cancel status persistence failed")

    tasker = await _make_tasker(FailingCancelledRepo())
    running = asyncio.Event()

    async def blocking_coro(ctx):
        running.set()
        await asyncio.Event().wait()

    task = await tasker.enqueue(name="active", task_type="demo", coroutine=blocking_coro)
    await running.wait()
    await _wait_status(tasker, task.id, {"running"})

    await asyncio.wait_for(tasker.shutdown(), timeout=1.0)

    assert (await tasker.get_task(task.id))["status"] == "cancelled"
    assert tasker._workers == []
    assert tasker._started is False


async def test_shutdown_exits_when_terminal_pruning_fails(monkeypatch):
    tasker = await _make_tasker(FakeRepo())
    running = asyncio.Event()

    async def blocking_coro(ctx):
        running.set()
        await asyncio.Event().wait()

    async def failing_prune():
        raise RuntimeError("terminal pruning failed")

    task = await tasker.enqueue(name="active", task_type="demo", coroutine=blocking_coro)
    await running.wait()
    await _wait_status(tasker, task.id, {"running"})
    monkeypatch.setattr(tasker, "_prune_terminal_tasks", failing_prune)

    await asyncio.wait_for(tasker.shutdown(), timeout=1.0)

    assert (await tasker.get_task(task.id))["status"] == "cancelled"
    assert tasker._workers == []
    assert tasker._started is False


async def test_cooperative_task_cancellation_keeps_worker_available():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)
    running = asyncio.Event()
    check_cancellation = asyncio.Event()

    async def cancellable_coro(ctx):
        running.set()
        await check_cancellation.wait()
        await ctx.raise_if_cancelled()

    cancelled = await tasker.enqueue(name="cancelled", task_type="demo", coroutine=cancellable_coro)
    await running.wait()
    assert await tasker.cancel_task(cancelled.id)
    check_cancellation.set()
    await _wait_status(tasker, cancelled.id, {"cancelled"})

    async def completed_coro(ctx):
        return "done"

    completed = await tasker.enqueue(name="completed", task_type="demo", coroutine=completed_coro)
    assert (await _wait_status(tasker, completed.id, {"success"}))["status"] == "success"
    await tasker.shutdown()


async def test_timeout_fails_task_and_releases_worker():
    repo = FakeRepo()
    tasker = await _make_tasker(repo, default_timeout_seconds=0.05)
    cancellation_reason: list[str | None] = []

    async def slow_coro(ctx):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            cancellation_reason.append(ctx.cancellation_reason)
            raise

    async def quick_coro(ctx):
        return "done"

    slow_task = await tasker.enqueue(name="slow", task_type="demo", coroutine=slow_coro)
    quick_task = await tasker.enqueue(name="quick", task_type="demo", coroutine=quick_coro)

    timed_out = await _wait_status(tasker, slow_task.id, {"failed"})
    completed = await _wait_status(tasker, quick_task.id, {"success"})

    assert timed_out["message"] == "任务执行超时"
    assert "0.05-second execution timeout" in timed_out["error"]
    assert cancellation_reason == ["timeout"]
    assert completed["result"] == "done"
    await tasker.shutdown()


async def test_enqueue_timeout_overrides_shorter_default():
    repo = FakeRepo()
    tasker = await _make_tasker(repo, default_timeout_seconds=0.01)

    async def coro(ctx):
        await asyncio.sleep(0.03)
        return "done"

    task = await tasker.enqueue(
        name="x",
        task_type="demo",
        coroutine=coro,
        timeout_seconds=0.2,
    )
    final = await _wait_status(tasker, task.id, {"success", "failed"})

    assert final["status"] == "success"
    assert final["result"] == "done"
    await tasker.shutdown()


async def test_task_coroutine_accepts_future_awaitable():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)
    loop = asyncio.get_running_loop()

    def future_coro(ctx):
        future = loop.create_future()
        future.set_result("done")
        return future

    task = await tasker.enqueue(name="x", task_type="demo", coroutine=future_coro)
    final = await _wait_status(tasker, task.id, {"success"})

    assert final["result"] == "done"
    await tasker.shutdown()


async def test_worker_cancellation_exposes_shutdown_reason():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)
    started = asyncio.Event()
    cancellation_reason: list[str | None] = []

    async def coro(ctx):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_reason.append(ctx.cancellation_reason)
            raise

    task = await tasker.enqueue(name="x", task_type="demo", coroutine=coro)
    await asyncio.wait_for(started.wait(), timeout=1)
    tasker._workers[0].cancel()
    final = await _wait_status(tasker, task.id, {"cancelled"})

    assert final["status"] == "cancelled"
    assert cancellation_reason == ["shutdown"]
    await tasker.shutdown()


async def test_completed_tasks_are_evicted_from_memory_without_deleting_persistent_history(monkeypatch):
    monkeypatch.setattr(task_service, "MAX_TERMINAL_TASKS", 3)
    repo = FakeRepo()
    tasker = await _make_tasker(repo)

    async def coro(ctx):
        return "ok"

    for index in range(6):
        task = await tasker.enqueue(name=f"t{index}", task_type="demo", coroutine=coro)
        await _wait_status(tasker, task.id, {"success"})

    listing = await tasker.list_tasks(limit=100)
    assert listing["summary"]["total"] == 6
    assert len(repo.deleted) == 0
    assert len(tasker._tasks) <= 3
    await tasker.shutdown()


async def test_load_state_marks_interrupted_and_evicts_only_memory_cache(monkeypatch):
    monkeypatch.setattr(task_service, "MAX_TERMINAL_TASKS", 2)
    repo = FakeRepo(
        preset=[
            FakeRecord(
                {"id": "a", "name": "a", "type": "demo", "status": "running", "created_at": "2026-01-01T00:00:05"}
            ),
            FakeRecord(
                {"id": "b", "name": "b", "type": "demo", "status": "success", "created_at": "2026-01-01T00:00:04"}
            ),
            FakeRecord(
                {"id": "c", "name": "c", "type": "demo", "status": "success", "created_at": "2026-01-01T00:00:03"}
            ),
            FakeRecord(
                {"id": "d", "name": "d", "type": "demo", "status": "success", "created_at": "2026-01-01T00:00:02"}
            ),
        ]
    )
    tasker = await _make_tasker(repo)

    # 中断的 running 任务被标记为 failed
    interrupted = await tasker.get_task("a")
    assert interrupted["status"] == "failed"
    # 仅在内存中保留最近 MAX_TERMINAL_TASKS 条终态任务，持久化历史保持完整
    listing = await tasker.list_tasks(limit=100)
    assert listing["summary"]["total"] == 4
    assert repo.deleted == []
    assert set(tasker._tasks) == {"a", "b"}
    await tasker.shutdown()


async def test_registered_resumable_task_is_requeued_after_restart():
    payload = {task_service.RESUMABLE_PAYLOAD_KEY: True, "job_id": "job-1"}
    repo = FakeRepo(
        preset=[
            FakeRecord(
                {
                    "id": "resumable-task",
                    "name": "可恢复任务",
                    "type": "resumable",
                    "status": "running",
                    "payload": payload,
                    "created_at": "2026-01-01T00:00:00",
                }
            )
        ]
    )
    tasker = Tasker(worker_count=1)
    tasker._repo = repo
    observed_payloads: list[dict] = []

    async def resume(context):
        observed_payloads.append(context.payload)
        return "resumed"

    tasker.register_resumable_handler("resumable", resume)
    await tasker.start()
    try:
        completed = await _wait_status(tasker, "resumable-task", {"success"})
        assert completed["result"] == "resumed"
        assert observed_payloads == [payload]
    finally:
        await tasker.shutdown()


async def test_resumable_task_survives_worker_shutdown_and_runs_on_next_startup():
    repo = FakeRepo()
    first_tasker = Tasker(worker_count=1)
    first_tasker._repo = repo
    started = asyncio.Event()

    async def resumed(context):
        return f"resumed:{context.payload['job_id']}"

    async def running_before_shutdown(context):
        started.set()
        await asyncio.Event().wait()

    first_tasker.register_resumable_handler("resumable", resumed)
    await first_tasker.start()
    task = await first_tasker.enqueue(
        name="可恢复任务",
        task_type="resumable",
        payload={"job_id": "job-2"},
        coroutine=running_before_shutdown,
    )
    await started.wait()
    await _wait_status(first_tasker, task.id, {"running"})
    await first_tasker.shutdown()

    assert repo.records[task.id]["status"] == "pending"
    assert repo.records[task.id]["payload"][task_service.RESUMABLE_PAYLOAD_KEY] is True

    second_tasker = Tasker(worker_count=1)
    second_tasker._repo = repo
    second_tasker.register_resumable_handler("resumable", resumed)
    await second_tasker.start()
    try:
        completed = await _wait_status(second_tasker, task.id, {"success"})
        assert completed["result"] == "resumed:job-2"
    finally:
        await second_tasker.shutdown()


async def test_same_tasker_restart_discards_stale_non_resumable_queue_entries():
    repo = FakeRepo()
    tasker = Tasker(worker_count=1)
    tasker._repo = repo
    running = asyncio.Event()
    queued_started = asyncio.Event()

    async def blocking(context):
        running.set()
        await asyncio.Event().wait()

    async def queued(context):
        queued_started.set()

    await tasker.start()
    await tasker.enqueue(name="running", task_type="demo", coroutine=blocking)
    queued_task = await tasker.enqueue(name="queued", task_type="demo", coroutine=queued)
    await running.wait()
    await tasker.shutdown()

    await tasker.start()
    try:
        await asyncio.sleep(0.05)
        restarted = await tasker.get_task(queued_task.id)

        assert restarted["status"] == "failed"
        assert restarted["message"] == "服务重启时任务未继续执行"
        assert not queued_started.is_set()
    finally:
        await tasker.shutdown()


async def test_same_tasker_restart_requeues_queued_resumable_task_once():
    repo = FakeRepo()
    tasker = Tasker(worker_count=1)
    tasker._repo = repo
    running = asyncio.Event()
    resume_calls = 0

    async def blocking(context):
        running.set()
        await asyncio.Event().wait()

    async def resume(context):
        nonlocal resume_calls
        resume_calls += 1
        return "resumed"

    tasker.register_resumable_handler("resumable", resume)
    await tasker.start()
    await tasker.enqueue(name="running", task_type="demo", coroutine=blocking)
    queued_task = await tasker.enqueue(
        name="queued resumable",
        task_type="resumable",
        coroutine=resume,
    )
    await running.wait()
    await tasker.shutdown()

    await tasker.start()
    try:
        completed = await _wait_status(tasker, queued_task.id, {"success"})
        await asyncio.sleep(0.05)

        assert completed["result"] == "resumed"
        assert resume_calls == 1
    finally:
        await tasker.shutdown()
