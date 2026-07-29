from copy import deepcopy
from types import SimpleNamespace

import pytest

from yuxi.knowledge.eval import service as evaluation_module
from yuxi.knowledge.eval.service import EvaluationService, EvaluationTaskError


class FakeContext:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {}
        self.task_id = "task-1"
        self.cancellation_reason = None
        self.messages: list[str] = []

    async def raise_if_cancelled(self) -> None:
        return None

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_message(self, message: str) -> None:
        self.messages.append(message)

    async def set_result(self, result: dict) -> None:
        self.result = result

    def is_cancel_requested(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_experiment_creator_must_retain_access_to_every_variant_kb(monkeypatch):
    class Repo:
        async def list_experiment_variants(self, experiment_id: str):
            return [SimpleNamespace(kb_id="kb-source"), SimpleNamespace(kb_id="kb-other")]

    class Users:
        async def get_by_uid(self, uid: str):
            return SimpleNamespace(uid=uid, role="admin", department_id=1, is_deleted=False)

    async def check_accessible(user_info: dict, kb_id: str) -> bool:
        return kb_id == "kb-source"

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    monkeypatch.setattr(evaluation_module, "UserRepository", Users)
    monkeypatch.setattr(evaluation_module.knowledge_base, "check_accessible", check_accessible)

    experiment = SimpleNamespace(
        experiment_id="experiment-1",
        source_kb_id="kb-source",
        created_by="user-1",
    )

    with pytest.raises(PermissionError, match="kb-other"):
        await service._ensure_experiment_creator_can_write(experiment)


@pytest.mark.asyncio
async def test_external_baseline_failed_run_resumes_persisted_items():
    class Repo:
        def __init__(self):
            self.run_updates: list[tuple[str, dict]] = []

        async def get_run(self, run_id: str):
            return SimpleNamespace(run_id=run_id, status="failed")

        async def get_dataset(self, dataset_id: str):
            return SimpleNamespace(
                item_count=1,
                has_gold_chunks=True,
                has_gold_answers=False,
            )

        async def list_all_dataset_items(self, dataset_id: str):
            return [
                SimpleNamespace(
                    item_id="item-1",
                    query_text="question",
                    gold_chunk_ids=["chunk-1"],
                    gold_answer=None,
                )
            ]

        async def list_all_run_items(self, run_id: str):
            return [
                SimpleNamespace(
                    item_index=0,
                    dataset_item_id="item-1",
                    metrics={"recall@1": 1.0, "f1@1": 1.0},
                )
            ]

        async def update_run(self, run_id: str, values: dict) -> None:
            self.run_updates.append((run_id, deepcopy(values)))

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    experiment = SimpleNamespace(
        experiment_id="experiment-1",
        name="experiment",
        source_kb_id="kb-1",
        dataset_fingerprint="dataset-fingerprint",
        corpus_snapshot={"fingerprint": "corpus-fingerprint", "retrieval_identity": "chunk_id"},
        shared_config={},
        created_by="user-1",
    )
    variant = SimpleNamespace(
        variant_id="variant-1",
        run_id="run-1",
        dataset_id="dataset-1",
        execution_type="external_rag",
        retrieval_config={},
        provenance={},
        input_snapshot={
            "dataset_fingerprint": "dataset-fingerprint",
            "corpus_fingerprint": "corpus-fingerprint",
            "results": [
                {
                    "item_id": "item-1",
                    "query": "question",
                    "generated_answer": "",
                    "retrieved_document_hashes": [],
                }
            ],
        },
    )

    run_id = await service._run_external_experiment_variant(
        FakeContext(),
        experiment=experiment,
        variant=variant,
        progress_start=0,
        progress_end=90,
    )

    assert run_id == "run-1"
    assert service.eval_repo.run_updates[0] == (
        "run-1",
        {"status": "running", "metrics": {}, "overall_score": None, "completed_at": None},
    )
    assert service.eval_repo.run_updates[-1][1]["status"] == "completed"
    assert service.eval_repo.run_updates[-1][1]["completed_items"] == 1


@pytest.mark.asyncio
async def test_evaluation_task_rejects_creator_after_kb_access_is_revoked(monkeypatch):
    class Repo:
        def __init__(self):
            self.run_updates: list[tuple[str, dict]] = []

        async def get_dataset(self, dataset_id: str):
            pytest.fail("权限失效后不应读取评估数据集")

        async def update_run(self, run_id: str, values: dict) -> None:
            self.run_updates.append((run_id, deepcopy(values)))

    class Users:
        async def get_by_uid(self, uid: str):
            return SimpleNamespace(uid=uid, role="admin", department_id=1, is_deleted=False)

    async def check_accessible(user_info: dict, kb_id: str) -> bool:
        return False

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    context = FakeContext(
        {
            "run_id": "run-1",
            "kb_id": "kb-1",
            "dataset_id": "dataset-1",
            "retrieval_config": {},
            "created_by": "user-1",
        }
    )
    monkeypatch.setattr(evaluation_module, "UserRepository", Users)
    monkeypatch.setattr(evaluation_module.knowledge_base, "check_accessible", check_accessible)

    with pytest.raises(EvaluationTaskError, match="kb-1"):
        await service._run_evaluation_task(context)

    assert service.eval_repo.run_updates[-1][1]["status"] == "failed"
    assert "kb-1" in service.eval_repo.run_updates[-1][1]["metrics"]["error"]


@pytest.mark.asyncio
async def test_dataset_generation_rejects_deleted_creator_before_resuming(monkeypatch):
    class Repo:
        def __init__(self):
            self.dataset_updates: list[tuple[str, dict]] = []

        async def count_dataset_items(self, dataset_id: str):
            pytest.fail("创建者失效后不应读取或继续生成评估数据集")

        async def update_dataset(self, dataset_id: str, values: dict) -> None:
            self.dataset_updates.append((dataset_id, deepcopy(values)))

    class Users:
        async def get_by_uid(self, uid: str):
            return SimpleNamespace(uid=uid, role="admin", department_id=1, is_deleted=True)

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    context = FakeContext(
        {
            "dataset_id": "dataset-1",
            "kb_id": "kb-1",
            "created_by": "user-1",
            "count": 2,
            "neighbors_count": 1,
            "concurrency_count": 1,
            "llm_model_spec": "provider:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        }
    )
    monkeypatch.setattr(evaluation_module, "UserRepository", Users)

    with pytest.raises(EvaluationTaskError, match="已被删除"):
        await service._generate_dataset_task(context)

    build_metadata = service.eval_repo.dataset_updates[-1][1]["build_metadata"]
    assert build_metadata["status"] == "failed"
    assert "已被删除" in build_metadata["error_message"]


@pytest.mark.asyncio
async def test_experiment_recovery_adopts_existing_active_task_id(monkeypatch):
    experiment = SimpleNamespace(
        experiment_id="experiment-1",
        task_id="stale-task",
        name="experiment",
        source_kb_id="kb-1",
        dataset_id="dataset-1",
    )

    class Repo:
        def __init__(self):
            self.experiment_updates: list[tuple[str, dict]] = []

        async def list_recoverable_experiments(self):
            return [experiment]

        async def reset_running_experiment_variants(self, experiment_id: str) -> None:
            return None

        async def update_experiment(self, experiment_id: str, values: dict) -> None:
            self.experiment_updates.append((experiment_id, deepcopy(values)))

    async def get_task(task_id: str):
        return {"status": "completed", "payload": {}, "cancel_requested": False}

    async def enqueue_unique_by_payload(**kwargs):
        return SimpleNamespace(id="active-task"), False

    async def allow_creator(experiment_row) -> None:
        return None

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    service._ensure_experiment_creator_can_write = allow_creator
    monkeypatch.setattr(evaluation_module.tasker, "get_task", get_task)
    monkeypatch.setattr(evaluation_module.tasker, "enqueue_unique_by_payload", enqueue_unique_by_payload)

    assert await service.recover_experiments() == 0
    assert service.eval_repo.experiment_updates == [("experiment-1", {"task_id": "active-task"})]


@pytest.mark.asyncio
async def test_evaluation_failure_does_not_persist_provider_secret():
    secret = "Authorization=secret-api-key provider-body=<private>"

    class Repo:
        def __init__(self):
            self.run_updates: list[tuple[str, dict]] = []

        async def get_dataset(self, dataset_id: str):
            raise RuntimeError(secret)

        async def update_run(self, run_id: str, values: dict) -> None:
            self.run_updates.append((run_id, deepcopy(values)))

    async def allow_creator(*args, **kwargs) -> None:
        return None

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    service._ensure_creator_can_write = allow_creator
    context = FakeContext(
        {
            "run_id": "run-1",
            "kb_id": "kb-1",
            "dataset_id": "dataset-1",
            "retrieval_config": {},
            "created_by": "user-1",
        }
    )

    with pytest.raises(EvaluationTaskError, match="RAG 评估任务执行失败"):
        await service._run_evaluation_task(context)

    assert service.eval_repo.run_updates[-1][1]["metrics"] == {"error": "RAG 评估任务执行失败"}
    assert context.messages == ["Error: RAG 评估任务执行失败"]
    assert secret not in repr(service.eval_repo.run_updates)


@pytest.mark.asyncio
async def test_dataset_state_failure_does_not_persist_provider_secret():
    secret = "Authorization=secret-api-key provider-body=<private>"

    class Repo:
        def __init__(self):
            self.dataset_updates: list[tuple[str, dict]] = []

        async def count_dataset_items(self, dataset_id: str):
            raise RuntimeError(secret)

        async def update_dataset(self, dataset_id: str, values: dict) -> None:
            self.dataset_updates.append((dataset_id, deepcopy(values)))

    async def allow_creator(*args, **kwargs) -> None:
        return None

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    service._ensure_creator_can_write = allow_creator
    context = FakeContext(
        {
            "dataset_id": "dataset-1",
            "kb_id": "kb-1",
            "created_by": "user-1",
            "count": 2,
            "neighbors_count": 1,
            "concurrency_count": 1,
            "llm_model_spec": "provider:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        }
    )

    with pytest.raises(EvaluationTaskError, match="评估数据集生成状态读取失败"):
        await service._generate_dataset_task(context)

    metadata = service.eval_repo.dataset_updates[-1][1]["build_metadata"]
    assert metadata["error_message"] == "评估数据集生成状态读取失败"
    assert secret not in repr(service.eval_repo.dataset_updates)


@pytest.mark.asyncio
async def test_experiment_variant_passes_original_creator_to_evaluation_task():
    captured = {}

    class Repo:
        async def get_dataset(self, dataset_id: str):
            return SimpleNamespace(item_count=1)

        async def create_run(self, values: dict) -> None:
            captured["run"] = values

        async def update_experiment_variant(self, variant_id: str, values: dict) -> None:
            captured["variant"] = values

    async def allow_corpus(*args, **kwargs) -> None:
        return None

    async def run_evaluation(context) -> None:
        captured["payload"] = context.payload

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    service._ensure_experiment_variant_corpus = allow_corpus
    service._run_evaluation_task = run_evaluation
    experiment = SimpleNamespace(
        experiment_id="experiment-1",
        name="Experiment",
        source_kb_id="kb-1",
        shared_config={},
        corpus_snapshot={"retrieval_identity": "chunk_id"},
        created_by="admin-1",
    )
    variant = SimpleNamespace(
        variant_id="variant-1",
        name="Variant",
        execution_type="research_compass",
        retrieval_config={},
        kb_id="kb-1",
        dataset_id="dataset-1",
        run_id=None,
    )

    await service._run_experiment_variant(
        FakeContext(),
        experiment=experiment,
        variant=variant,
        progress_start=0,
        progress_end=90,
    )

    assert captured["payload"]["created_by"] == "admin-1"
    assert captured["run"]["created_by"] == "admin-1"


@pytest.mark.asyncio
async def test_experiment_recovery_enqueue_failure_is_persisted_without_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    experiment = SimpleNamespace(
        experiment_id="experiment-1",
        task_id="stale-task",
        name="experiment",
        source_kb_id="kb-1",
        dataset_id="dataset-1",
    )

    class Repo:
        def __init__(self):
            self.experiment_updates: list[tuple[str, dict]] = []

        async def list_recoverable_experiments(self):
            return [experiment]

        async def reset_running_experiment_variants(self, experiment_id: str) -> None:
            return None

        async def update_experiment(self, experiment_id: str, values: dict) -> None:
            self.experiment_updates.append((experiment_id, deepcopy(values)))

    async def get_task(task_id: str):
        return {"status": "completed", "payload": {}, "cancel_requested": False}

    async def fail_enqueue(**kwargs):
        raise RuntimeError(secret)

    async def allow_creator(experiment_row) -> None:
        return None

    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = Repo()
    service._ensure_experiment_creator_can_write = allow_creator
    monkeypatch.setattr(evaluation_module.tasker, "get_task", get_task)
    monkeypatch.setattr(evaluation_module.tasker, "enqueue_unique_by_payload", fail_enqueue)

    assert await service.recover_experiments() == 0
    assert service.eval_repo.experiment_updates[-1][1]["error_message"] == "消融实验恢复任务提交失败"
    assert secret not in repr(service.eval_repo.experiment_updates)
