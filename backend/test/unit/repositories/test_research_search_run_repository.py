from datetime import datetime
from types import SimpleNamespace

from yuxi.repositories.research_search_run_repository import ResearchSearchRunRepository


def test_search_run_serialization_separates_summary_from_result_snapshot():
    record = SimpleNamespace(
        run_id="run-1",
        kb_id="kb-research",
        raw_query="Which methods are reproducible?",
        rewritten_query="reproducible research methods",
        rewrite_keywords=["reproducibility"],
        model_config_json={"chat_model": "provider:model"},
        retrieval_config={"mode": "local_hybrid", "top_k": 10},
        status="success",
        stage_timings={"retrieval_ms": 12},
        result_count=2,
        result_snapshot={"items": [{"paper_id": "paper-1"}], "total": 1},
        is_pinned=True,
        error_type=None,
        error_message=None,
        created_at=datetime(2026, 7, 26, 10, 0, 0),
        started_at=datetime(2026, 7, 26, 10, 0, 1),
        completed_at=datetime(2026, 7, 26, 10, 0, 2),
    )

    summary = ResearchSearchRunRepository.serialize(record)
    detail = ResearchSearchRunRepository.serialize(record, include_result=True)

    assert "result" not in summary
    assert summary["is_pinned"] is True
    assert summary["config"]["retrieval"]["mode"] == "local_hybrid"
    assert detail["result"] == record.result_snapshot
