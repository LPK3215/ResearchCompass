"""research_search_service 纯函数单元测试。

不依赖外部服务，只验证查询改写解析和检索结果聚合逻辑。
"""
from __future__ import annotations

import pytest

from yuxi.services.research_search_service import (
    ResearchSearchError,
    _aggregate_results,
    _parse_rewrite,
)


# ---------------------------------------------------------------------------
# _parse_rewrite
# ---------------------------------------------------------------------------

class TestParseRewrite:
    def test_valid_json_returns_rewritten_query_and_keywords(self):
        content = '{"rewritten_query": "graph neural network drug interaction", "keywords": ["GNN", "drug-target"]}'
        result = _parse_rewrite(content)
        assert result["rewritten_query"] == "graph neural network drug interaction"
        assert result["keywords"] == ["GNN", "drug-target"]

    def test_json_wrapped_in_markdown_code_block(self):
        content = '```json\n{"rewritten_query": "test query", "keywords": ["a"]}\n```'
        result = _parse_rewrite(content)
        assert result["rewritten_query"] == "test query"
        assert result["keywords"] == ["a"]

    def test_json_wrapped_in_plain_code_block(self):
        content = '```\n{"rewritten_query": "q", "keywords": ["k"]}\n```'
        result = _parse_rewrite(content)
        assert result["rewritten_query"] == "q"

    def test_keywords_truncated_to_20(self):
        import json
        keywords = [f"k{i}" for i in range(30)]
        content = json.dumps({"rewritten_query": "q", "keywords": keywords})
        result = _parse_rewrite(content)
        assert len(result["keywords"]) == 20

    def test_invalid_json_raises_error(self):
        with pytest.raises(ResearchSearchError) as exc_info:
            _parse_rewrite("not json at all")
        assert exc_info.value.error_type == "query_rewrite_invalid"

    def test_json_array_instead_of_object_raises_error(self):
        with pytest.raises(ResearchSearchError) as exc_info:
            _parse_rewrite('["rewritten_query", "keywords"]')
        assert exc_info.value.error_type == "query_rewrite_invalid"

    def test_missing_rewritten_query_raises_error(self):
        content = '{"keywords": ["a"]}'
        with pytest.raises(ResearchSearchError) as exc_info:
            _parse_rewrite(content)
        assert exc_info.value.error_type == "query_rewrite_invalid"

    def test_empty_rewritten_query_raises_error(self):
        content = '{"rewritten_query": "", "keywords": ["a"]}'
        with pytest.raises(ResearchSearchError) as exc_info:
            _parse_rewrite(content)
        assert exc_info.value.error_type == "query_rewrite_invalid"

    def test_missing_keywords_raises_error(self):
        content = '{"rewritten_query": "test"}'
        with pytest.raises(ResearchSearchError) as exc_info:
            _parse_rewrite(content)
        assert exc_info.value.error_type == "query_rewrite_invalid"

    def test_keywords_with_empty_string_raises_error(self):
        content = '{"rewritten_query": "q", "keywords": ["a", ""]}'
        with pytest.raises(ResearchSearchError) as exc_info:
            _parse_rewrite(content)
        assert exc_info.value.error_type == "query_rewrite_invalid"

    def test_keywords_with_non_string_raises_error(self):
        content = '{"rewritten_query": "q", "keywords": ["a", 123]}'
        with pytest.raises(ResearchSearchError) as exc_info:
            _parse_rewrite(content)
        assert exc_info.value.error_type == "query_rewrite_invalid"


# ---------------------------------------------------------------------------
# _aggregate_results
# ---------------------------------------------------------------------------

def _make_chunk(
    *,
    paper_id: str = "p1",
    title: str = "Test Paper",
    chunk_id: str = "c1",
    vector_score: float | None = None,
    bm25_score: float | None = None,
    hybrid_score: float | None = None,
    rerank_score: float | None = None,
    fusion_score: float | None = None,
    content: str = "chunk content",
) -> dict:
    chunk: dict = {
        "content": content,
        "metadata": {
            "paper": {
                "paper_id": paper_id,
                "title": title,
            },
            "chunk_id": chunk_id,
            "file_id": "f1",
            "chunk_index": 0,
        },
    }
    if vector_score is not None:
        chunk["vector_score"] = vector_score
    if bm25_score is not None:
        chunk["bm25_score"] = bm25_score
    if hybrid_score is not None:
        chunk["hybrid_score"] = hybrid_score
    if rerank_score is not None:
        chunk["rerank_score"] = rerank_score
    if fusion_score is not None:
        chunk["fusion_score"] = fusion_score
    return chunk


class TestAggregateResults:
    def test_single_chunk_single_paper_returns_one_result(self):
        chunks = [_make_chunk(vector_score=0.9)]
        results = _aggregate_results("kb1", chunks, top_k=10)
        assert len(results) == 1
        assert results[0]["paper_id"] == "p1"
        assert results[0]["title"] == "Test Paper"
        assert results[0]["scores"]["vector"] == 0.9

    def test_multiple_chunks_same_paper_merged(self):
        chunks = [
            _make_chunk(chunk_id="c1", vector_score=0.8),
            _make_chunk(chunk_id="c2", vector_score=0.9),
        ]
        results = _aggregate_results("kb1", chunks, top_k=10)
        assert len(results) == 1
        assert results[0]["scores"]["vector"] == 0.9  # max of two chunks
        assert len(results[0]["evidence"]) == 2

    def test_multiple_papers_sorted_by_ranking_score(self):
        chunks = [
            _make_chunk(paper_id="p1", chunk_id="c1", rerank_score=0.5),
            _make_chunk(paper_id="p2", chunk_id="c2", rerank_score=0.9),
        ]
        results = _aggregate_results("kb1", chunks, top_k=10)
        assert len(results) == 2
        assert results[0]["paper_id"] == "p2"  # higher score first

    def test_top_k_limits_results(self):
        chunks = [
            _make_chunk(paper_id=f"p{i}", chunk_id=f"c{i}", rerank_score=1.0 - i * 0.1)
            for i in range(5)
        ]
        results = _aggregate_results("kb1", chunks, top_k=3)
        assert len(results) == 3

    def test_evidence_limited_to_10_per_paper(self):
        chunks = [
            _make_chunk(chunk_id=f"c{i}", vector_score=0.5 + i * 0.01)
            for i in range(15)
        ]
        results = _aggregate_results("kb1", chunks, top_k=10)
        assert len(results[0]["evidence"]) == 10

    def test_evidence_sorted_by_max_score_descending(self):
        chunks = [
            _make_chunk(chunk_id="c1", vector_score=0.3),
            _make_chunk(chunk_id="c2", vector_score=0.9),
            _make_chunk(chunk_id="c3", vector_score=0.6),
        ]
        results = _aggregate_results("kb1", chunks, top_k=10)
        evidence = results[0]["evidence"]
        assert evidence[0]["chunk_id"] == "c2"  # highest score first
        assert evidence[1]["chunk_id"] == "c3"
        assert evidence[2]["chunk_id"] == "c1"

    def test_chunk_without_paper_id_raises_error(self):
        chunk = {"content": "x", "metadata": {"chunk_id": "c1"}}
        with pytest.raises(ResearchSearchError) as exc_info:
            _aggregate_results("kb1", [chunk], top_k=10)
        assert exc_info.value.error_type == "evidence_missing_paper"

    def test_evidence_includes_locator(self):
        chunks = [_make_chunk(chunk_id="c1")]
        results = _aggregate_results("kb1", chunks, top_k=10)
        locator = results[0]["evidence"][0]["locator"]
        assert locator["api_path"] == "/api/research/databases/kb1/papers/p1/evidence/c1"
        assert locator["chunk_id"] == "c1"

    def test_ranking_score_uses_rerank_over_fusion_over_hybrid(self):
        chunks = [
            _make_chunk(paper_id="p1", chunk_id="c1", rerank_score=0.8, fusion_score=0.5, hybrid_score=0.3),
        ]
        results = _aggregate_results("kb1", chunks, top_k=10)
        assert results[0]["ranking_score"] == 0.8

    def test_ranking_score_falls_back_to_fusion_without_rerank(self):
        chunks = [
            _make_chunk(paper_id="p1", chunk_id="c1", fusion_score=0.7, hybrid_score=0.3),
        ]
        results = _aggregate_results("kb1", chunks, top_k=10)
        assert results[0]["ranking_score"] == 0.7

    def test_ranking_score_falls_back_to_hybrid_without_rerank_fusion(self):
        chunks = [
            _make_chunk(paper_id="p1", chunk_id="c1", hybrid_score=0.6),
        ]
        results = _aggregate_results("kb1", chunks, top_k=10)
        assert results[0]["ranking_score"] == 0.6
