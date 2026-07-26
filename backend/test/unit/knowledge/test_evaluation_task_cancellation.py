import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from yuxi.knowledge.eval import service as evaluation_module
from yuxi.knowledge.eval.service import EvaluationService


class FakeContext:
    def __init__(self, payload: dict, *, cancel_requested: bool, cancellation_reason: str | None = None):
        self.payload = payload
        self.task_id = "task-1"
        self.cancel_requested = cancel_requested
        self.cancellation_reason = cancellation_reason
        self.messages: list[str] = []

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_message(self, message: str) -> None:
        self.messages.append(message)

    async def set_result(self, result: dict) -> None:
        self.result = result

    async def raise_if_cancelled(self) -> None:
        return None

    def is_cancel_requested(self) -> bool:
        return self.cancel_requested


class FakeEvaluationRepository:
    def __init__(self):
        self.dataset_updates: list[tuple[str, dict]] = []
        self.run_updates: list[tuple[str, dict]] = []

    async def update_dataset(self, dataset_id: str, data: dict) -> None:
        await asyncio.sleep(0)
        self.dataset_updates.append((dataset_id, deepcopy(data)))

    async def count_dataset_items(self, dataset_id: str) -> int:
        return 0

    async def get_dataset(self, dataset_id: str):
        raise asyncio.CancelledError

    async def update_run(self, run_id: str, data: dict) -> None:
        await asyncio.sleep(0)
        self.run_updates.append((run_id, deepcopy(data)))


@pytest.fixture(autouse=True)
def allow_task_creator_for_cancellation_tests(monkeypatch):
    async def ensure_creator_can_write(self, creator_uid: str, kb_ids: set[str]) -> None:
        return None

    monkeypatch.setattr(EvaluationService, "_ensure_creator_can_write", ensure_creator_can_write)


async def test_dataset_cancellation_updates_build_status(monkeypatch):
    repo = FakeEvaluationRepository()
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    context = FakeContext(
        {
            "dataset_id": "dataset-1",
            "kb_id": "kb-1",
            "count": 10,
            "neighbors_count": 1,
            "concurrency_count": 1,
            "llm_model_spec": "provider:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        },
        cancel_requested=True,
    )

    loading = asyncio.Event()

    async def cancelled_get_kb(kb_id: str):
        loading.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(evaluation_module.knowledge_base, "aget_kb", cancelled_get_kb)

    task = asyncio.create_task(service._generate_dataset_task(context))
    await asyncio.wait_for(loading.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    build_metadata = repo.dataset_updates[-1][1]["build_metadata"]
    assert build_metadata["status"] == "failed"
    assert build_metadata["error_message"] == "任务已取消"


async def test_evaluation_timeout_updates_run_status():
    repo = FakeEvaluationRepository()
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    context = FakeContext(
        {
            "run_id": "run-1",
            "kb_id": "kb-1",
            "dataset_id": "dataset-1",
            "retrieval_config": {},
        },
        cancel_requested=False,
        cancellation_reason="timeout",
    )

    loading = asyncio.Event()

    async def cancelled_get_dataset(dataset_id: str):
        loading.set()
        await asyncio.Event().wait()

    repo.get_dataset = cancelled_get_dataset
    task = asyncio.create_task(service._run_evaluation_task(context))
    await asyncio.wait_for(loading.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run_id, update = repo.run_updates[-1]
    assert run_id == "run-1"
    assert update["status"] == "failed"
    assert update["metrics"] == {"error": "任务执行超时"}
    assert context.messages == ["Error: 任务执行超时"]


async def test_dataset_shutdown_keeps_generation_recoverable(monkeypatch):
    repo = FakeEvaluationRepository()
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    context = FakeContext(
        {
            "dataset_id": "dataset-1",
            "kb_id": "kb-1",
            "count": 10,
            "neighbors_count": 1,
            "concurrency_count": 1,
            "llm_model_spec": "provider:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        },
        cancel_requested=False,
        cancellation_reason="shutdown",
    )

    loading = asyncio.Event()

    async def cancelled_get_kb(kb_id: str):
        loading.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(evaluation_module.knowledge_base, "aget_kb", cancelled_get_kb)

    task = asyncio.create_task(service._generate_dataset_task(context))
    await asyncio.wait_for(loading.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    build_metadata = repo.dataset_updates[-1][1]["build_metadata"]
    assert build_metadata["status"] == "pending"
    assert build_metadata["message"] == "服务重启，任务将继续"


async def test_evaluation_shutdown_keeps_run_recoverable():
    repo = FakeEvaluationRepository()
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    context = FakeContext(
        {
            "run_id": "run-1",
            "kb_id": "kb-1",
            "dataset_id": "dataset-1",
            "retrieval_config": {},
        },
        cancel_requested=False,
        cancellation_reason="shutdown",
    )

    loading = asyncio.Event()

    async def cancelled_get_dataset(dataset_id: str):
        loading.set()
        await asyncio.Event().wait()

    repo.get_dataset = cancelled_get_dataset
    task = asyncio.create_task(service._run_evaluation_task(context))
    await asyncio.wait_for(loading.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run_id, update = repo.run_updates[-1]
    assert run_id == "run-1"
    assert update == {"status": "running", "completed_at": None}


async def test_evaluation_resume_reuses_persisted_item_without_new_model_call(monkeypatch):
    class ResumeRepository:
        def __init__(self):
            self.run_updates: list[tuple[str, dict]] = []

        async def get_dataset(self, dataset_id: str):
            return SimpleNamespace(kb_id="kb-1", has_gold_chunks=True, has_gold_answers=False)

        async def list_all_dataset_items(self, dataset_id: str):
            return [
                SimpleNamespace(
                    item_id="dataset-item-1",
                    query_text="question",
                    gold_chunk_ids=["chunk-1"],
                    gold_answer=None,
                )
            ]

        async def list_all_run_items(self, run_id: str):
            return [
                SimpleNamespace(
                    item_index=0,
                    dataset_item_id="dataset-item-1",
                    metrics={"recall@1": 1.0, "recall@10": 1.0, "f1@1": 1.0, "f1@10": 1.0},
                )
            ]

        async def update_run(self, run_id: str, data: dict) -> None:
            self.run_updates.append((run_id, deepcopy(data)))

    repo = ResumeRepository()
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    context = FakeContext(
        {
            "run_id": "run-1",
            "kb_id": "kb-1",
            "dataset_id": "dataset-1",
            "retrieval_config": {},
        },
        cancel_requested=False,
    )

    async def fake_get_kb(kb_id: str):
        return SimpleNamespace()

    async def fail_if_evaluated(**_kwargs):
        pytest.fail("已持久化的评估题目不应再次调用检索或模型")

    monkeypatch.setattr(evaluation_module.knowledge_base, "aget_kb", fake_get_kb)
    monkeypatch.setattr(evaluation_module, "evaluate_question", fail_if_evaluated)

    await service._run_evaluation_task(context)

    assert context.result["completed_items"] == 1
    assert repo.run_updates[0] == ("run-1", {"status": "running", "completed_at": None})
    assert repo.run_updates[-1][1]["status"] == "completed"
    assert repo.run_updates[-1][1]["completed_items"] == 1
