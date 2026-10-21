"""research_paper_metadata_service 的补全行为测试。

覆盖字段缺失判定、Crossref 归一化与严格匹配、只填空字段、纯元数据记录跳过分块重建，
以及索引任务提交失败时显式失败，不依赖数据库与外部网络。
"""

from types import SimpleNamespace

import asyncio
import pytest

from yuxi.services import research_paper_metadata_service as metadata_service


def _paper(**overrides):
    base = {
        "paper_id": "paper-1",
        "file_id": "file-1",
        "title": "Attention Is All You Need",
        "doi": None,
        "abstract": None,
        "authors": [],
        "publication_year": None,
        "venue": None,
        "keywords": [],
        "citation_count": None,
        "metadata_revision": 1,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_missing_fields_only_lists_empty_values():
    paper = _paper(authors=["Ashish Vaswani"], citation_count=0)

    missing = metadata_service.missing_paper_fields(paper)

    assert "authors" not in missing
    assert "citation_count" not in missing
    assert set(missing) == {"doi", "abstract", "publication_year", "venue", "keywords"}


def test_crossref_values_normalize_external_payload():
    item = {
        "DOI": "10.1000/ABC",
        "title": ["Attention Is All You Need"],
        "author": [{"given": "Ashish", "family": "Vaswani"}, {"name": "The Consortium"}],
        "container-title": ["Advances in Neural Information Processing Systems"],
        "published-print": {"date-parts": [[2017, 6]]},
        "abstract": "<jats:p>Hello <jats:italic>world</jats:italic></jats:p>",
        "subject": ["NLP", "NLP", "Machine Learning"],
        "is-referenced-by-count": 100,
    }

    values = metadata_service.crossref_values(item)

    assert values["doi"] == "10.1000/abc"
    assert values["authors"] == ["Ashish Vaswani", "The Consortium"]
    assert values["publication_year"] == 2017
    assert values["venue"] == "Advances in Neural Information Processing Systems"
    assert values["abstract"] == "Hello world"
    assert values["keywords"] == ["NLP", "Machine Learning"]
    assert values["citation_count"] == 100


def test_crossref_values_ignore_invalid_citation_count():
    values = metadata_service.crossref_values({"DOI": "10.1/x", "is-referenced-by-count": -3})

    assert "citation_count" not in values


def test_select_crossref_item_prefers_matching_year():
    paper = _paper(publication_year=2017)
    items = [
        {"DOI": "10.1/old", "title": ["Attention is all you need"], "issued": {"date-parts": [[2016]]}},
        {"DOI": "10.1/new", "title": ["Attention Is All You Need"], "issued": {"date-parts": [[2017]]}},
    ]

    item = metadata_service.select_crossref_item(items, paper)

    assert item["DOI"] == "10.1/new"


def test_select_crossref_item_returns_none_on_ambiguous_match():
    paper = _paper(publication_year=2017)
    items = [
        {"DOI": "10.1/a", "title": ["Attention Is All You Need"], "issued": {"date-parts": [[2017]]}},
        {"DOI": "10.1/b", "title": ["Attention Is All You Need"], "issued": {"date-parts": [[2017]]}},
    ]

    assert metadata_service.select_crossref_item(items, paper) is None


class FakeRepository:
    def __init__(self, paper, updated=None):
        self.paper = paper
        self.updated = updated or paper
        self.update_calls = []
        self.update_metadata_calls = []
        self.fail_calls = []

    async def get_by_paper_id(self, **kwargs):
        return self.paper

    async def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return SimpleNamespace(**{**vars(self.paper), **kwargs["data"]})

    async def update_metadata(self, **kwargs):
        self.update_metadata_calls.append(kwargs)
        return SimpleNamespace(**{**vars(self.paper), **kwargs["data"], "metadata_revision": 2})

    async def fail_reindex(self, **kwargs):
        self.fail_calls.append(kwargs)


def _patch_enrichment(monkeypatch, repo, crossref, semantic=None):
    async def allow_access(*args, **kwargs):
        return None

    async def lookup_crossref(paper):
        return crossref, "title" if crossref else "none"

    async def lookup_semantic(paper):
        return semantic or {}

    monkeypatch.setattr(metadata_service, "_ensure_access", allow_access)
    monkeypatch.setattr(metadata_service, "AcademicPaperRepository", lambda: repo)
    monkeypatch.setattr(metadata_service, "_lookup_crossref", lookup_crossref)
    monkeypatch.setattr(metadata_service, "_lookup_semantic_scholar", lookup_semantic)
    monkeypatch.setattr(metadata_service, "_serialize_paper", lambda paper: {"paper_id": paper.paper_id})


@pytest.mark.asyncio
async def test_enrich_fills_only_missing_fields_and_keeps_existing(monkeypatch):
    paper = _paper(venue="Existing Venue")
    repo = FakeRepository(paper)
    enqueued = []

    async def enqueue(**kwargs):
        enqueued.append(kwargs)
        return SimpleNamespace(id="task-1", status="pending"), True

    _patch_enrichment(
        monkeypatch,
        repo,
        {"venue": "New Venue", "doi": "10.1000/abc", "citation_count": 12},
    )
    monkeypatch.setattr(metadata_service, "_enqueue_paper_reindex", enqueue)

    result = await metadata_service.enrich_paper_metadata(
        kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="admin-1")
    )

    assert repo.update_metadata_calls[0]["data"]["doi"] == "10.1000/abc"
    assert "venue" not in repo.update_metadata_calls[0]["data"]
    assert result["filled"] == {"doi": "10.1000/abc", "citation_count": 12}
    assert result["field_sources"] == {"doi": "crossref", "citation_count": "crossref"}
    assert result["reindex_task"] == {"task_id": "task-1", "status": "pending", "created": True}
    assert enqueued[0]["revision"] == 2


@pytest.mark.asyncio
async def test_enrich_metadata_only_record_skips_reindex(monkeypatch):
    paper = _paper(file_id=None, metadata_revision=1)
    repo = FakeRepository(paper)

    async def reject_enqueue(**kwargs):
        raise AssertionError("纯元数据记录不应提交分块重建任务")

    _patch_enrichment(monkeypatch, repo, {"doi": "10.1000/abc"})
    monkeypatch.setattr(metadata_service, "_enqueue_paper_reindex", reject_enqueue)

    result = await metadata_service.enrich_paper_metadata(
        kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="admin-1")
    )

    assert repo.update_calls[0]["data"] == {"doi": "10.1000/abc"}
    assert repo.update_metadata_calls == []
    assert result["reindex_task"] is None
    assert "重建" not in result["message"]


@pytest.mark.asyncio
async def test_enrich_returns_early_when_metadata_complete(monkeypatch):
    paper = _paper(
        doi="10.1000/abc",
        abstract="Abstract",
        authors=["Ashish Vaswani"],
        publication_year=2017,
        venue="NeurIPS",
        keywords=["NLP"],
        citation_count=100,
    )

    async def reject_lookup(paper):
        raise AssertionError("元数据完整时不应查询外部数据源")

    _patch_enrichment(monkeypatch, FakeRepository(paper), {})
    monkeypatch.setattr(metadata_service, "_lookup_crossref", reject_lookup)

    result = await metadata_service.enrich_paper_metadata(
        kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="admin-1")
    )

    assert result["filled"] == {}
    assert result["missing_fields"] == []
    assert result["message"] == "论文元数据已完整，无需补全"


@pytest.mark.asyncio
async def test_enrich_raises_when_no_source_has_missing_fields(monkeypatch):
    repo = FakeRepository(_paper())
    _patch_enrichment(monkeypatch, repo, {}, {})

    with pytest.raises(metadata_service.ResearchPaperMetadataError) as exc_info:
        await metadata_service.enrich_paper_metadata(
            kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="admin-1")
        )

    assert exc_info.value.error_type == "metadata_not_found"
    assert repo.update_metadata_calls == []


@pytest.mark.asyncio
async def test_enrich_uses_semantic_scholar_for_fields_crossref_misses(monkeypatch):
    repo = FakeRepository(_paper())
    _patch_enrichment(monkeypatch, repo, {"doi": "10.1000/abc"}, {"citation_count": 7})

    result = await metadata_service.enrich_paper_metadata(
        kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="admin-1")
    )

    assert result["field_sources"]["doi"] == "crossref"
    assert result["field_sources"]["citation_count"] == "semantic_scholar"
    assert {"source": "semantic_scholar", "match": "resolved"} in result["sources"]


@pytest.mark.asyncio
async def test_enrich_marks_failure_when_reindex_cannot_be_enqueued(monkeypatch):
    repo = FakeRepository(_paper())

    async def fail_enqueue(**kwargs):
        raise RuntimeError("queue unavailable")

    _patch_enrichment(monkeypatch, repo, {"doi": "10.1000/abc"})
    monkeypatch.setattr(metadata_service, "_enqueue_paper_reindex", fail_enqueue)

    with pytest.raises(metadata_service.ResearchPaperMetadataError) as exc_info:
        await metadata_service.enrich_paper_metadata(
            kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="admin-1")
        )

    assert exc_info.value.error_type == "metadata_reindex_enqueue_failed"
    assert repo.fail_calls[0]["error"] == "论文元数据同步任务提交失败"


@pytest.mark.asyncio
async def test_lookup_crossref_falls_back_to_title_search(monkeypatch):
    item = {"DOI": "10.1/x", "title": ["Attention Is All You Need"], "issued": {"date-parts": [[2017]]}}

    class FakeClient:
        async def get_by_doi(self, doi):
            return None

        async def search_by_title(self, title):
            return [item]

    monkeypatch.setattr(metadata_service, "CrossrefClient", FakeClient)

    values, match = await metadata_service._lookup_crossref(_paper(doi="10.1/x"))

    assert match == "title"
    assert values["doi"] == "10.1/x"


@pytest.mark.asyncio
async def test_lookup_crossref_reports_unavailable_without_raising(monkeypatch):
    class FailingClient:
        async def get_by_doi(self, doi):
            raise metadata_service.ResearchPaperMetadataError("crossref_unavailable", "Crossref 请求失败")

        async def search_by_title(self, title):
            raise AssertionError("DOI 查询失败后不再发起标题检索")

    monkeypatch.setattr(metadata_service, "CrossrefClient", FailingClient)

    values, match = await metadata_service._lookup_crossref(_paper(doi="10.1/x"))

    assert values == {}
    assert match == "unavailable"


@pytest.mark.asyncio
async def test_lookup_semantic_scholar_returns_empty_on_timeout(monkeypatch):
    class SlowClient:
        async def resolve_paper(self, paper):
            await asyncio.sleep(0.05)
            return {"citationCount": 3}

    monkeypatch.setattr(metadata_service, "SemanticScholarClient", SlowClient)
    monkeypatch.setattr(metadata_service, "SEMANTIC_SCHOLAR_TIMEOUT_SECONDS", 0.01)

    assert await metadata_service._lookup_semantic_scholar(_paper()) == {}
