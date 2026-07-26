import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.knowledge.eval.service import EvaluationService, _corpus_fingerprint


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_export_corpus_manifest_is_deterministic_and_uses_only_indexed_files():
    service = EvaluationService()
    service.kb_repo.get_by_kb_id = AsyncMock(
        return_value=SimpleNamespace(kb_id="kb-research", kb_type="milvus", embedding_model_spec="embed:model")
    )
    service.file_repo.list_by_kb_id = AsyncMock(
        return_value=[
            SimpleNamespace(
                filename="paper-b.pdf",
                content_hash="hash-b",
                chunk_count=12,
                file_size=2048,
                status="done",
                is_folder=False,
            ),
            SimpleNamespace(
                filename="ignored.pdf",
                content_hash="hash-ignored",
                chunk_count=1,
                file_size=10,
                status="failed",
                is_folder=False,
            ),
            SimpleNamespace(
                filename="paper-a.pdf",
                content_hash="hash-a",
                chunk_count=8,
                file_size=1024,
                status="indexed",
                is_folder=False,
            ),
        ]
    )

    exported = await service.export_corpus_manifest("kb-research")
    manifest = json.loads(exported["content"])

    assert exported["filename"] == f"researchcompass-corpus-{_corpus_fingerprint(['hash-a', 'hash-b'])[:12]}.json"
    assert manifest == {
        "schema_version": "research_compass_corpus_manifest.v1",
        "kb_id": "kb-research",
        "corpus_fingerprint": _corpus_fingerprint(["hash-a", "hash-b"]),
        "embedding_model_spec": "embed:model",
        "document_count": 2,
        "documents": [
            {"filename": "paper-a.pdf", "content_hash": "hash-a", "chunk_count": 8, "file_size": 1024},
            {"filename": "paper-b.pdf", "content_hash": "hash-b", "chunk_count": 12, "file_size": 2048},
        ],
    }


async def test_export_corpus_manifest_rejects_empty_indexed_corpus():
    service = EvaluationService()
    service.kb_repo.get_by_kb_id = AsyncMock(
        return_value=SimpleNamespace(kb_id="kb-empty", kb_type="milvus", embedding_model_spec="embed:model")
    )
    service.file_repo.list_by_kb_id = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="没有可用于评估的已索引文档"):
        await service.export_corpus_manifest("kb-empty")
