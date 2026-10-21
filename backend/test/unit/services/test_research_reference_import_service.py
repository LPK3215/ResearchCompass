"""research_reference_import_service 解析与去重纯函数单元测试。

不依赖数据库与外部服务，只验证 BibTeX / RIS 解析、字段归一化与去重标识。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import yuxi.services.research_reference_import_service as reference_import_service
from yuxi.services.research_reference_import_service import (
    ResearchReferenceImportError,
    detect_reference_format,
    import_references,
    parse_references,
    reference_identity,
)

BIBTEX_SAMPLE = r"""
@comment{ignored}
@article{vaswani2017attention,
  title = {Attention Is All You Need},
  author = {Vaswani, Ashish and Shazeer, Noam and Parmar, Niki},
  year = {2017},
  journal = {Advances in Neural Information Processing Systems},
  doi = {https://doi.org/10.48550/arXiv.1706.03762},
  keywords = {Transformer; Attention; NLP},
  abstract = {The dominant sequence transduction models are based on complex recurrent networks.}
}

@inproceedings{lee2023rag,
  title = "Retrieval-Augmented {G}eneration for {S}cientific {W}riting",
  author = {Lee, Min and Kim, Jae},
  year = 2023,
  booktitle = {Proceedings of the Conference on Empirical Methods},
}
"""

RIS_SAMPLE = """TY  - JOUR
TI  - A Survey on Retrieval-Augmented Generation
AU  - Gao, Yunfan
AU  - Xiong, Yun
PY  - 2024
JO  - arXiv preprint
DO  - 10.48550/arXiv.2312.10997
KW  - RAG
KW  - Survey
AB  - This survey reviews retrieval-augmented generation.
ER  -
"""


class TestDetectReferenceFormat:
    def test_bibtex_detected(self):
        assert detect_reference_format(BIBTEX_SAMPLE) == "bibtex"

    def test_ris_detected(self):
        assert detect_reference_format(RIS_SAMPLE) == "ris"

    def test_empty_content_rejected(self):
        with pytest.raises(ResearchReferenceImportError) as exc_info:
            detect_reference_format("   \n")
        assert exc_info.value.error_type == "reference_content_empty"

    def test_unknown_format_rejected(self):
        with pytest.raises(ResearchReferenceImportError) as exc_info:
            detect_reference_format("just plain text")
        assert exc_info.value.error_type == "reference_format_unknown"


class TestParseBibtex:
    def test_fields_parsed_and_normalized(self):
        references, errors = parse_references(BIBTEX_SAMPLE)
        assert errors == []
        assert len(references) == 2
        first = references[0]
        assert first.title == "Attention Is All You Need"
        assert first.authors == ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"]
        assert first.publication_year == 2017
        assert first.venue == "Advances in Neural Information Processing Systems"
        assert first.doi == "10.48550/arxiv.1706.03762"
        assert first.keywords == ["Transformer", "Attention", "NLP"]

    def test_quoted_and_nested_braces(self):
        references, errors = parse_references(BIBTEX_SAMPLE)
        assert errors == []
        assert references[1].title == "Retrieval-Augmented Generation for Scientific Writing"
        assert references[1].publication_year == 2023

    def test_missing_title_reported_per_entry(self):
        content = """
        @article{ok, title={Valid Title}, year={2024}}
        @article{bad, year={2024}}
        """
        references, errors = parse_references(content)
        assert [item.title for item in references] == ["Valid Title"]
        assert len(errors) == 1
        assert errors[0]["index"] == 2
        assert errors[0]["error_type"] == "reference_title_missing"

    def test_empty_file_rejected(self):
        with pytest.raises(ResearchReferenceImportError) as exc_info:
            parse_references("@comment{only comments}")
        assert exc_info.value.error_type == "reference_entries_empty"


class TestParseRis:
    def test_fields_parsed(self):
        references, errors = parse_references(RIS_SAMPLE)
        assert errors == []
        assert len(references) == 1
        reference = references[0]
        assert reference.title == "A Survey on Retrieval-Augmented Generation"
        assert reference.authors == ["Yunfan Gao", "Yun Xiong"]
        assert reference.publication_year == 2024
        assert reference.venue == "arXiv preprint"
        assert reference.doi == "10.48550/arxiv.2312.10997"
        assert reference.keywords == ["RAG", "Survey"]


class TestReferenceIdentity:
    def test_doi_takes_priority(self):
        references, _ = parse_references(BIBTEX_SAMPLE)
        assert reference_identity(references[0]) == "doi:10.48550/arxiv.1706.03762"

    def test_title_normalized_when_doi_missing(self):
        references, _ = parse_references(BIBTEX_SAMPLE)
        identity = reference_identity(references[1])
        assert identity == "title:retrievalaugmentedgenerationforscientificwriting"


class _FakeRepository:
    def __init__(self, existing: list[SimpleNamespace]):
        self._existing = existing
        self.created_rows: list[dict] = []

    async def iter_export_batches(self, *, kb_id: str, include_archived: bool = False):
        yield self._existing

    async def create_metadata_only_batch(self, *, kb_id: str, rows: list[dict]):
        self.created_rows = rows
        return [SimpleNamespace(**row) for row in rows], 0


class TestImportReferences:
    async def test_existing_doi_skipped_and_new_entry_imported(self, monkeypatch):
        existing = [SimpleNamespace(doi="10.48550/arXiv.1706.03762", title="Attention Is All You Need")]
        repo = _FakeRepository(existing)
        monkeypatch.setattr(reference_import_service, "_ensure_access", AsyncMock())
        monkeypatch.setattr(reference_import_service, "AcademicPaperRepository", lambda: repo)

        result = await import_references(
            kb_id="kb_demo",
            content=BIBTEX_SAMPLE,
            reference_format="bibtex",
            current_user=SimpleNamespace(uid="u1"),
        )

        assert result["imported_count"] == 1
        assert result["skipped_count"] == 1
        assert result["failed_count"] == 0
        assert len(repo.created_rows) == 1
        row = repo.created_rows[0]
        assert row["file_id"] is None
        assert row["paper_id"].startswith("ref_")
        assert row["metadata_source"] == "reference_file"
        assert row["indexed_revision"] == 0

    async def test_invalid_entry_counted_as_failed(self, monkeypatch):
        repo = _FakeRepository([])
        monkeypatch.setattr(reference_import_service, "_ensure_access", AsyncMock())
        monkeypatch.setattr(reference_import_service, "AcademicPaperRepository", lambda: repo)

        result = await import_references(
            kb_id="kb_demo",
            content="@article{bad, year={2024}}",
            reference_format="bibtex",
            current_user=SimpleNamespace(uid="u1"),
        )

        assert result["imported_count"] == 0
        assert result["failed_count"] == 1
        assert result["failed"][0]["error_type"] == "reference_title_missing"
