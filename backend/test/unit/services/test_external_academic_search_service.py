"""外部多源检索归并与降级行为测试。"""

import pytest

from yuxi.services import external_academic_search_service as search_service
from yuxi.services.semantic_scholar_service import SemanticScholarError


def _item(title, *, doi=None, venue=None, citation_count=None, sources=None):
    return {
        "paper_id": f"paper-{title}",
        "title": title,
        "doi": doi,
        "venue": venue,
        "citation_count": citation_count,
        "abstract": None,
        "authors": [],
        "publication_year": 2024,
        "is_open_access": False,
        "open_access_url": None,
        "external_ids": {},
    }


def test_merge_source_results_dedupes_by_doi_and_keeps_sources():
    merged = search_service.merge_source_results(
        [
            ("semantic_scholar", [_item("Attention", doi="10.1000/abc")]),
            ("openalex", [_item("Attention Is All You Need", doi="10.1000/ABC", venue="NeurIPS", citation_count=9)]),
            ("crossref", [_item("Attention Is All You Need", doi="10.1000/abc", citation_count=5)]),
        ]
    )

    assert len(merged) == 1
    assert merged[0]["sources"] == ["semantic_scholar", "openalex", "crossref"]
    assert merged[0]["venue"] == "NeurIPS"
    assert merged[0]["citation_count"] == 9


def test_semantic_scholar_item_falls_back_to_arxiv_pdf():
    item = search_service._semantic_scholar_item(
        {
            "paperId": "s2-1",
            "title": "Attention Is All You Need",
            "externalIds": {"ArXiv": "1706.03762"},
            "isOpenAccess": False,
            "openAccessPdf": None,
        }
    )

    assert item["open_access_url"] == "https://arxiv.org/pdf/1706.03762.pdf"
    assert item["is_open_access"] is True


def test_merge_source_results_falls_back_to_normalized_title():
    merged = search_service.merge_source_results(
        [
            ("semantic_scholar", [_item("Graph Neural Networks!")]),
            ("openalex", [_item("graph neural networks")]),
        ]
    )

    assert len(merged) == 1
    assert merged[0]["sources"] == ["semantic_scholar", "openalex"]


@pytest.mark.asyncio
async def test_search_multi_source_tolerates_single_source_failure(monkeypatch):
    class FakeSemanticScholarClient:
        async def search(self, query, limit):
            raise SemanticScholarError("semantic_scholar_rate_limited", "Semantic Scholar API 触发限流")

    async def openalex(query, limit):
        return [_item("Paper A", doi="10.1/a")]

    async def crossref(query, limit):
        return [_item("Paper B", doi="10.1/b")]

    monkeypatch.setattr(search_service, "SemanticScholarClient", FakeSemanticScholarClient)
    monkeypatch.setattr(search_service, "search_openalex", openalex)
    monkeypatch.setattr(search_service, "search_crossref", crossref)

    result = await search_service.search_multi_source("graph", 10)

    assert result["partial"] is True
    assert {item["title"] for item in result["items"]} == {"Paper A", "Paper B"}
    failed = [source for source in result["sources"] if source["status"] == "failed"]
    assert failed == [
        {
            "source": "semantic_scholar",
            "label": "Semantic Scholar",
            "status": "failed",
            "retryable": True,
            "message": "Semantic Scholar API 触发限流",
        }
    ]


@pytest.mark.asyncio
async def test_search_multi_source_raises_retryable_error_when_all_sources_fail(monkeypatch):
    class FakeSemanticScholarClient:
        async def search(self, query, limit):
            raise SemanticScholarError("semantic_scholar_timeout", "Semantic Scholar 请求超时")

    async def failing_openalex(query, limit):
        raise search_service.ExternalSearchSourceError("openalex", "OpenAlex 请求超时")

    async def failing_crossref(query, limit):
        raise search_service.ExternalSearchSourceError("crossref", "Crossref 请求超时")

    monkeypatch.setattr(search_service, "SemanticScholarClient", FakeSemanticScholarClient)
    monkeypatch.setattr(search_service, "search_openalex", failing_openalex)
    monkeypatch.setattr(search_service, "search_crossref", failing_crossref)

    with pytest.raises(search_service.ExternalSearchSourceError) as exc_info:
        await search_service.search_multi_source("graph", 10)

    assert exc_info.value.source == "all_sources"
