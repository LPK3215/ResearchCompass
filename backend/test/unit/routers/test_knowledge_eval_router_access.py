from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_eval_router


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.mark.parametrize(
    ("endpoint", "write"),
    [
        (knowledge_eval_router.download_evaluation_dataset, False),
        (knowledge_eval_router.delete_evaluation_dataset, True),
    ],
)
async def test_dataset_id_routes_authorize_owning_kb_before_access(monkeypatch, endpoint, write):
    calls = {"access": [], "export": 0, "delete": 0}

    class FakeEvaluationRepository:
        async def get_dataset(self, dataset_id: str):
            assert dataset_id == "dataset-other"
            return SimpleNamespace(kb_id="kb-owner")

    class FakeEvaluationService:
        eval_repo = FakeEvaluationRepository()

        async def export_dataset_jsonl(self, _dataset_id: str):
            calls["export"] += 1
            return {"filename": "dataset.jsonl", "content": ""}

        async def delete_dataset(self, _dataset_id: str):
            calls["delete"] += 1

    async def deny_access(current_user, kb_id: str, *, write: bool = False):
        calls["access"].append((current_user.uid, kb_id, write))
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(knowledge_eval_router, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(knowledge_eval_router, "_ensure_access", deny_access)

    with pytest.raises(HTTPException) as exc_info:
        await endpoint("dataset-other", current_user=SimpleNamespace(uid="admin-a"))

    assert exc_info.value.status_code == 403
    assert calls == {
        "access": [("admin-a", "kb-owner", write)],
        "export": 0,
        "delete": 0,
    }


async def test_evaluation_router_does_not_expose_internal_exception(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"

    class FakeEvaluationService:
        async def list_datasets(self, kb_id: str):
            raise RuntimeError(secret)

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(knowledge_eval_router, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(knowledge_eval_router, "_ensure_access", allow_access)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_eval_router.list_evaluation_datasets(
            "kb-1",
            current_user=SimpleNamespace(uid="admin-a"),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "获取评估数据集列表失败"
    assert secret not in str(exc_info.value.detail)
