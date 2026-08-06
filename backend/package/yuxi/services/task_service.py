import asyncio
import math
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from yuxi.repositories.task_repository import TaskRepository
from yuxi.utils.datetime_utils import coerce_any_to_utc_datetime, utc_isoformat
from yuxi.utils.logging_config import logger

TaskCoroutine = Callable[["TaskContext"], Awaitable[Any]]
TERMINAL_STATUSES = {"success", "failed", "cancelled"}
RESUMABLE_PAYLOAD_KEY = "_tasker_resumable"
# 纯进度推进时，进度增量小于该阈值则只更新内存、不落库（前端读内存，不受影响）
PROGRESS_PERSIST_DELTA = 2.0
# 内存保留最近多少条终态任务，持久化历史由显式管理员删除或保留策略治理。
MAX_TERMINAL_TASKS = 200
# 后台任务默认最多执行 6 小时，可按部署环境或单个任务覆盖。
TASKER_DEFAULT_TIMEOUT_SECONDS = float(os.getenv("TASKER_DEFAULT_TIMEOUT_SECONDS", 6 * 60 * 60))
# 哨兵：区分「未传参」与「显式传入 None」，使 result/error 可被清空
_UNSET: Any = object()


class _TaskExecutionTimeout(TimeoutError):
    pass


class PublicTaskError(RuntimeError):
    """A deliberately sanitized failure that may be shown in task details."""

    def __init__(self, message: str, *, retryable: bool = False, dependency: str | None = None):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.dependency = dependency


def _public_task_error_message(exc: Exception) -> str:
    if isinstance(exc, PublicTaskError):
        return exc.message
    return "任务执行失败，请稍后重试"


def _iso_to_utc_naive(value: str | None) -> datetime | None:
    if not value:
        return None
    return coerce_any_to_utc_datetime(value).replace(tzinfo=None)


@dataclass
class Task:
    id: str
    name: str
    type: str
    status: str = "pending"
    progress: float = 0.0
    message: str = ""
    created_at: str = field(default_factory=utc_isoformat)
    updated_at: str = field(default_factory=utc_isoformat)
    started_at: str | None = None
    completed_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    result: Any | None = None
    error: str | None = None
    retryable: bool = False
    dependency: str | None = None
    retry_count: int = 0
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_summary_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("payload", None)
        data.pop("result", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            id=data["id"],
            name=data.get("name", "Unnamed Task"),
            type=data.get("type", "general"),
            status=data.get("status", "pending"),
            progress=data.get("progress", 0.0),
            message=data.get("message", ""),
            created_at=data.get("created_at", utc_isoformat()),
            updated_at=data.get("updated_at", utc_isoformat()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            payload=data.get("payload", {}),
            result=data.get("result"),
            error=data.get("error"),
            retryable=bool(data.get("retryable", False)),
            dependency=data.get("dependency"),
            retry_count=int(data.get("retry_count", 0) or 0),
            cancel_requested=bool(data.get("cancel_requested", False)),
        )


class TaskContext:
    def __init__(self, tasker: "Tasker", task_id: str, payload: dict[str, Any] | None = None):
        self._tasker = tasker
        self.task_id = task_id
        self.payload = payload or {}
        self.cancellation_reason: str | None = None

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        await self._tasker._update_task(
            self.task_id,
            progress=max(0.0, min(progress, 100.0)),
            message=message,
        )

    async def set_message(self, message: str) -> None:
        await self._tasker._update_task(self.task_id, message=message)

    async def set_result(self, result: Any) -> None:
        await self._tasker._update_task(self.task_id, result=result)

    async def update_payload(self, values: dict[str, Any]) -> None:
        """Persist task payload progress needed to resume a multi-stage task safely."""
        if not isinstance(values, dict):
            raise TypeError("Task payload updates must be a mapping")
        await self._tasker._update_task(self.task_id, payload=values)

    def is_cancel_requested(self) -> bool:
        return self._tasker._is_cancel_requested(self.task_id)

    async def raise_if_cancelled(self) -> None:
        if await self._tasker._is_cancellation_requested_persisted(self.task_id):
            self.cancellation_reason = "cancelled"
            raise asyncio.CancelledError("Task was cancelled")


class Tasker:
    def __init__(
        self,
        worker_count: int = 2,
        default_timeout_seconds: float = TASKER_DEFAULT_TIMEOUT_SECONDS,
    ):
        self.worker_count = max(1, worker_count)
        self.default_timeout_seconds = self._validate_timeout_seconds(default_timeout_seconds)
        self._queue: asyncio.Queue[tuple[str, TaskCoroutine, float]] = asyncio.Queue()
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._workers: list[asyncio.Task[Any]] = []
        self._started = False
        self._repo = TaskRepository()
        # 记录每个任务上次落库时的进度，用于进度节流
        self._last_persisted_progress: dict[str, float] = {}
        # 只有显式注册且在 payload 中标记的任务才能在进程重启后重投递。
        self._resumable_handlers: dict[str, TaskCoroutine] = {}

    def register_resumable_handler(self, task_type: str, handler: TaskCoroutine) -> None:
        normalized_type = task_type.strip()
        if not normalized_type:
            raise ValueError("Task type must not be empty")
        self._resumable_handlers[normalized_type] = handler

    async def start(self) -> None:
        async with self._lifecycle_lock:
            async with self._lock:
                if self._started:
                    return
                await self._load_state()
                for _ in range(self.worker_count):
                    worker = asyncio.create_task(self._worker_loop(), name="tasker-worker")
                    self._workers.append(worker)
                self._started = True
                logger.info("Tasker started with {} workers", self.worker_count)

    async def shutdown(self) -> None:
        async with self._lifecycle_lock:
            async with self._lock:
                if not self._started:
                    return
                workers = self._workers.copy()
                self._workers.clear()
                self._started = False
                for worker in workers:
                    worker.cancel()

            await asyncio.gather(*workers, return_exceptions=True)
            async with self._lock:
                # Worker cancellation only removes the item currently being processed. Any
                # queued callables belong to the stopped event loop generation and must not
                # survive a same-process restart; _load_state() will reconstruct only the
                # explicitly resumable work from durable state.
                self._queue = asyncio.Queue()
                self._tasks.clear()
                self._last_persisted_progress.clear()
            logger.info("Tasker shutdown complete")

    async def enqueue(
        self,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
        coroutine: TaskCoroutine,
        timeout_seconds: float | None = None,
    ) -> Task:
        effective_timeout = self._resolve_timeout_seconds(timeout_seconds)
        task_id = uuid.uuid4().hex
        task = Task(id=task_id, name=name, type=task_type, payload=self._prepare_payload(task_type, payload))
        async with self._lock:
            self._tasks[task_id] = task
            await self._persist_task(task)
            await self._queue.put((task_id, coroutine, effective_timeout))
        logger.info("Enqueued task {} ({})", task_id, name)
        return task

    async def find_task_by_payload(
        self,
        *,
        task_type: str,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
    ) -> Task | None:
        record = await self._repo.find_by_payload(
            task_type=task_type,
            payload_match=payload_match,
            statuses=statuses,
        )
        return Task.from_dict(record.to_dict()) if record else None

    async def find_task_by_payload_any_type(
        self,
        *,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
    ) -> Task | None:
        record = await self._repo.find_any_by_payload(
            payload_match=payload_match,
            statuses=statuses,
        )
        return Task.from_dict(record.to_dict()) if record else None

    async def enqueue_unique_by_payload(
        self,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
        coroutine: TaskCoroutine,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[Task, bool]:
        effective_timeout = self._resolve_timeout_seconds(timeout_seconds)
        task_payload = self._prepare_payload(task_type, payload)
        async with self._lock:
            task_id = uuid.uuid4().hex
            task = Task(id=task_id, name=name, type=task_type, payload=task_payload)
            record, created = await self._repo.create_or_get_by_payload(
                task_id=task_id,
                data=self._task_persistence_data(task),
                task_type=task_type,
                payload_match=payload_match,
                statuses=statuses,
            )
            if not created:
                existing = Task.from_dict(record.to_dict())
                self._tasks[existing.id] = existing
                return existing, False
            self._tasks[task_id] = task
            await self._queue.put((task_id, coroutine, effective_timeout))
        logger.info("Enqueued task {} ({})", task.id, name)
        return task, True

    def _find_task_by_payload_locked(
        self,
        task_type: str,
        payload_match: dict[str, Any],
        statuses: set[str] | None,
    ) -> Task | None:
        for task in self._tasks.values():
            if task.type != task_type:
                continue
            if statuses is not None and task.status not in statuses:
                continue
            if all(task.payload.get(key) == value for key, value in payload_match.items()):
                return task
        return None

    async def list_tasks(self, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        limited_tasks = [
            Task.from_dict(record.to_dict()) for record in await self._repo.list(status=status, limit=max(limit, 0))
        ]
        summary = await self._repo.get_list_summary(status=status)

        return {
            "tasks": [task.to_summary_dict() for task in limited_tasks],
            "summary": summary,
        }

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        record = await self._repo.get_by_id(task_id)
        return Task.from_dict(record.to_dict()).to_dict() if record else None

    async def cancel_task(self, task_id: str) -> bool:
        record = await self._repo.request_cancellation(task_id)
        if record is None:
            return False
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.cancel_requested = True
                task.updated_at = record.to_dict()["updated_at"]
        logger.info("Cancellation requested for task {}", task_id)
        return True

    async def retry_task(self, task_id: str, *, max_retries: int = 3) -> dict[str, Any] | None:
        """Requeue a failed resumable task without creating a second task record."""
        async with self._lock:
            task = self._tasks.get(task_id)
            task_type = task.type if task is not None else None
            if task_type is None:
                record = await self._repo.get_by_id(task_id)
                task_type = str(record.to_dict().get("type")) if record else None
            if task_type is None:
                return None
            handler = self._resumable_handlers.get(task_type)
            if handler is None:
                return None
            record = await self._repo.claim_retry(task_id, max_retries=max_retries)
            if record is None:
                return None
            task = Task.from_dict(record.to_dict())
            await self._queue.put((task.id, handler, self.default_timeout_seconds))
            self._tasks[task.id] = task
            return task.to_dict()

    async def delete_task(self, task_id: str) -> bool:
        """Delete a terminal task by id. Active work must first finish or cancel."""
        deleted = await self._repo.delete_terminal(task_id, TERMINAL_STATUSES)
        if not deleted:
            return False
        async with self._lock:
            self._tasks.pop(task_id, None)
            self._last_persisted_progress.pop(task_id, None)
        logger.info("Deleted task {}", task_id)
        return True

    async def delete_terminal_tasks_by_payload(self, *, payload_match: dict[str, Any]) -> int:
        deleted_ids = await self._repo.delete_by_payload(
            payload_match=payload_match,
            statuses=TERMINAL_STATUSES,
        )
        if not deleted_ids:
            return 0

        async with self._lock:
            for task_id in deleted_ids:
                self._tasks.pop(task_id, None)
                self._last_persisted_progress.pop(task_id, None)
        logger.info("Deleted {} terminal task(s) for removed resource", len(deleted_ids))
        return len(deleted_ids)

    async def _worker_loop(self) -> None:
        while True:
            try:
                task_id, coroutine, timeout_seconds = await self._queue.get()
                task: Task | None = None
                try:
                    task = await self._get_task_instance(task_id)
                    if not task:
                        continue
                    if await self._is_cancellation_requested_persisted(task_id):
                        await self._mark_cancelled(task_id, "Task was cancelled before execution")
                        continue
                    await self._update_task(
                        task_id, status="running", progress=0.0, message="任务开始执行", started_at=utc_isoformat()
                    )
                    context = TaskContext(self, task_id, task.payload)
                    try:
                        result = await self._run_task_coroutine(coroutine, context, timeout_seconds)
                        if await self._is_cancellation_requested_persisted(task_id):
                            completion_message = "任务已完成（取消请求在安全检查点后到达）"
                        else:
                            completion_message = "任务已完成"
                        await self._update_task(
                            task_id,
                            status="success",
                            progress=100.0,
                            message=completion_message,
                            result=result,
                            completed_at=utc_isoformat(),
                        )
                    except _TaskExecutionTimeout as exc:
                        logger.warning("Task {} timed out after {} seconds", task_id, timeout_seconds)
                        await self._update_task(
                            task_id,
                            status="failed",
                            progress=100.0,
                            message="任务执行超时",
                            error=str(exc),
                            completed_at=utc_isoformat(),
                        )
                    except asyncio.CancelledError:
                        worker = asyncio.current_task()
                        should_stop_worker = worker is not None and worker.cancelling() > 0
                        if should_stop_worker:
                            should_resume = task is not None and self._is_resumable_task(task)
                            cancellation_requested = await self._is_cancellation_requested_persisted(task_id)
                            if should_resume and not cancellation_requested:
                                await self._mark_pending_for_restart(task_id)
                            else:
                                await self._mark_cancelled(task_id, "任务被取消")
                            raise
                        await self._mark_cancelled(task_id, "任务被取消")
                    except Exception as exc:  # noqa: BLE001
                        public_error = _public_task_error_message(exc)
                        logger.error(
                            "Task {} failed: exception_type={} error_type={}",
                            task_id,
                            type(exc).__name__,
                            getattr(exc, "error_type", "unhandled_task_error"),
                        )
                        await self._update_task(
                            task_id,
                            status="failed",
                            progress=100.0,
                            message="任务执行失败",
                            error=public_error,
                            retryable=bool(getattr(exc, "retryable", False)),
                            dependency=getattr(exc, "dependency", None),
                            completed_at=utc_isoformat(),
                        )
                finally:
                    self._queue.task_done()
                    await self._prune_terminal_tasks()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("Tasker worker error: exception_type={}", type(exc).__name__)
                worker = asyncio.current_task()
                if worker is not None and worker.cancelling() > 0:
                    break

    async def _run_task_coroutine(
        self,
        coroutine: TaskCoroutine,
        context: TaskContext,
        timeout_seconds: float,
    ) -> Any:
        execution = asyncio.ensure_future(coroutine(context))
        try:
            done, _ = await asyncio.wait({execution}, timeout=timeout_seconds)
            if execution not in done:
                context.cancellation_reason = "timeout"
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                raise _TaskExecutionTimeout(f"Task exceeded the {timeout_seconds:g}-second execution timeout")
            return await execution
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                context.cancellation_reason = "shutdown"
                execution.cancel()
                current_task.uncancel()
                try:
                    await asyncio.gather(execution, return_exceptions=True)
                finally:
                    current_task.cancel()
            raise

    def _resolve_timeout_seconds(self, timeout_seconds: float | None) -> float:
        if timeout_seconds is None:
            return self.default_timeout_seconds
        return self._validate_timeout_seconds(timeout_seconds)

    @staticmethod
    def _validate_timeout_seconds(timeout_seconds: float) -> float:
        timeout_seconds = float(timeout_seconds)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Task timeout must be a positive finite number")
        return timeout_seconds

    async def _get_task_instance(self, task_id: str) -> Task | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def _mark_cancelled(self, task_id: str, message: str) -> None:
        await self._update_task(
            task_id,
            status="cancelled",
            progress=100.0,
            message=message,
            completed_at=utc_isoformat(),
        )

    async def _mark_pending_for_restart(self, task_id: str) -> None:
        await self._update_task(
            task_id,
            status="pending",
            message="服务正在重启，任务将在启动后继续",
            result=None,
            error=None,
        )

    async def _update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        result: Any = _UNSET,
        error: Any = _UNSET,
        retryable: bool | None = None,
        dependency: str | None | Any = _UNSET,
        payload: dict[str, Any] | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            previous_status = task.status
            if status:
                task.status = status
            if progress is not None:
                task.progress = max(0.0, min(progress, 100.0))
            if message is not None:
                task.message = message
            if result is not _UNSET:
                task.result = result
            if error is not _UNSET:
                task.error = error
            if retryable is not None:
                task.retryable = retryable
            if dependency is not _UNSET:
                task.dependency = dependency
            if payload is not None:
                task.payload.update(payload)
            if started_at is not None:
                task.started_at = started_at
            if completed_at is not None:
                task.completed_at = completed_at
            task.updated_at = utc_isoformat()

            # 仅进度推进的高频更新做节流；状态切换、结果、错误、起止时间一律立即落库
            only_progress = (
                status is None and result is _UNSET and error is _UNSET and started_at is None and completed_at is None
                and payload is None
            )
            if only_progress:
                last = self._last_persisted_progress.get(task_id)
                if last is not None and abs(task.progress - last) < PROGRESS_PERSIST_DELTA:
                    return
            self._last_persisted_progress[task_id] = task.progress
            await self._persist_task(task)
            if status in TERMINAL_STATUSES and previous_status not in TERMINAL_STATUSES:
                uid = str(task.payload.get("uid") or "")
                if uid:
                    try:
                        from yuxi.services.notification_service import create_notification
                        notification_by_status = {
                            "success": ("task_success", "任务已完成"),
                            "failed": ("task_failed", "任务执行失败"),
                            "cancelled": ("task_cancelled", "任务已取消"),
                        }
                        notification_type, title = notification_by_status[status]
                        await create_notification(
                            recipient_uid=uid,
                            notification_type=notification_type,
                            title=title,
                            message=task.message or title,
                            resource_type="task", resource_id=task.id,
                            idempotency_key=f"task:{task.id}:{status}",
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to persist task notification: error_type={}", type(exc).__name__)

    def _is_cancel_requested(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return bool(task and task.cancel_requested)

    async def _is_cancellation_requested_persisted(self, task_id: str) -> bool:
        if self._is_cancel_requested(task_id):
            return True
        record = await self._repo.get_by_id(task_id)
        persisted_task = Task.from_dict(record.to_dict()) if record is not None else None
        if persisted_task is None or not persisted_task.cancel_requested:
            return False
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.cancel_requested = True
        return True

    def _collect_stale_terminal_ids(self) -> list[str]:
        """从内存中剔除超出保留上限的旧终态任务。"""
        terminal = [task for task in self._tasks.values() if task.status in TERMINAL_STATUSES]
        if len(terminal) <= MAX_TERMINAL_TASKS:
            return []
        terminal.sort(key=lambda item: item.created_at or "", reverse=True)
        stale = terminal[MAX_TERMINAL_TASKS:]
        for task in stale:
            self._tasks.pop(task.id, None)
            self._last_persisted_progress.pop(task.id, None)
        return [task.id for task in stale]

    async def _prune_terminal_tasks(self) -> None:
        async with self._lock:
            stale_ids = self._collect_stale_terminal_ids()
        if stale_ids:
            logger.info("Evicted {} old terminal tasks from memory cache", len(stale_ids))

    async def _load_state(self) -> None:
        records = await self._repo.list_for_startup(
            terminal_limit=MAX_TERMINAL_TASKS,
            terminal_statuses=TERMINAL_STATUSES,
        )
        interrupted = 0
        requeued = 0
        for record in records:
            task = Task.from_dict(record.to_dict())
            if task.status not in TERMINAL_STATUSES:
                handler = self._resumable_handlers.get(task.type) if self._is_resumable_task(task) else None
                if task.cancel_requested:
                    task.status = "cancelled"
                    task.progress = 100.0
                    task.message = "服务重启时确认任务已取消"
                    task.completed_at = utc_isoformat()
                    task.updated_at = utc_isoformat()
                    await self._persist_task(task)
                elif handler is not None:
                    task.status = "pending"
                    task.progress = 0.0
                    task.message = "服务重启，任务已重新排队"
                    task.result = None
                    task.error = None
                    task.started_at = None
                    task.completed_at = None
                    task.updated_at = utc_isoformat()
                    await self._persist_task(task)
                    await self._queue.put((task.id, handler, self.default_timeout_seconds))
                    requeued += 1
                else:
                    # 未注册任务没有可安全重建的执行函数，必须明确失败而不是伪造恢复。
                    task.message = "服务重启时任务中断" if task.status == "running" else "服务重启时任务未继续执行"
                    task.status = "failed"
                    task.updated_at = utc_isoformat()
                    await self._persist_task(task)
                    interrupted += 1
            self._tasks[task.id] = task
        if interrupted:
            logger.info("Marked {} interrupted tasks as failed", interrupted)
        if requeued:
            logger.info("Requeued {} resumable tasks after restart", requeued)
        stale_ids = self._collect_stale_terminal_ids()
        if stale_ids:
            logger.info("Evicted {} old terminal tasks from memory cache on startup", len(stale_ids))

    async def _persist_task(self, task: Task) -> None:
        await self._repo.upsert(task.id, self._task_persistence_data(task))

    def _prepare_payload(self, task_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        task_payload = dict(payload or {})
        if task_type in self._resumable_handlers:
            task_payload[RESUMABLE_PAYLOAD_KEY] = True
        return task_payload

    def _is_resumable_task(self, task: Task) -> bool:
        return bool(task.payload.get(RESUMABLE_PAYLOAD_KEY)) and task.type in self._resumable_handlers

    @staticmethod
    def _task_persistence_data(task: Task) -> dict[str, Any]:
        return {
            "name": task.name,
            "type": task.type,
            "status": task.status,
            "progress": task.progress,
            "message": task.message,
            "payload": task.payload,
            "result": task.result,
            "error": task.error,
            "retryable": task.retryable,
            "dependency": task.dependency,
            "retry_count": task.retry_count,
            "cancel_requested": 1 if task.cancel_requested else 0,
            "created_at": _iso_to_utc_naive(task.created_at),
            "updated_at": _iso_to_utc_naive(task.updated_at),
            "started_at": _iso_to_utc_naive(task.started_at),
            "completed_at": _iso_to_utc_naive(task.completed_at),
        }


tasker = Tasker()


__all__ = ["tasker", "TaskContext", "Tasker"]
