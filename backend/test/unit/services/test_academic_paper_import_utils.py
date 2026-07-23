"""academic_paper_import_service 纯函数单元测试。

不依赖外部 API 或数据库，只验证文件名生成、元数据提取和开放获取 URL 解析。
"""
from __future__ import annotations

import pytest

from yuxi.services.academic_paper_import_service import (
    AcademicPaperImportError,
    _open_access_url,
    _paper_metadata,
    _safe_filename,
)


# ---------------------------------------------------------------------------
# _safe_filename
# ---------------------------------------------------------------------------

class TestSafeFilename:
    def test_normal_title_returns_pdf_filename(self):
        result = _safe_filename("Attention Is All You Need", "abc123def456")
        assert result.endswith(".pdf")
        assert "Attention Is All You Need" in result

    def test_title_with_special_chars_sanitized(self):
        result = _safe_filename("Title: With / Special * Chars?", "abc123def456")
        assert "/" not in result
        assert "*" not in result
        assert "?" not in result
        assert ":" not in result

    def test_empty_title_uses_fallback(self):
        result = _safe_filename("", "abc123def456")
        assert result.startswith("academic-paper-")
        assert result.endswith(".pdf")

    def test_none_title_uses_fallback(self):
        result = _safe_filename(None, "abc123def456")
        assert result.startswith("academic-paper-")

    def test_long_title_truncated(self):
        long_title = "A" * 300
        result = _safe_filename(long_title, "abc123def456")
        # filename part (before .pdf) should be at most ~180 chars + paper_id prefix
        name_part = result.rsplit(".", 1)[0]
        assert len(name_part) < 250

    def test_paper_id_included_in_filename(self):
        result = _safe_filename("Test Paper", "abc123def456")
        assert "abc123def456"[:12] in result


# ---------------------------------------------------------------------------
# _paper_metadata
# ---------------------------------------------------------------------------

class TestPaperMetadata:
    def test_full_paper_dict_returns_metadata(self):
        paper = {
            "paperId": "abc123",
            "title": "Test Paper",
            "abstract": "An abstract",
            "authors": [{"name": "Alice"}, {"name": "Bob"}],
            "year": 2023,
            "venue": "ICML",
            "externalIds": {"DOI": "10.1000/xyz", "ArXiv": "2301.00001"},
            "fieldsOfStudy": ["Computer Science", "AI"],
            "citationCount": 42,
        }
        result = _paper_metadata(paper)
        assert result["paper_id"] == "s2_abc123"
        assert result["title"] == "Test Paper"
        assert result["abstract"] == "An abstract"
        assert result["authors"] == ["Alice", "Bob"]
        assert result["publication_year"] == 2023
        assert result["venue"] == "ICML"
        assert result["doi"] == "10.1000/xyz"
        assert result["keywords"] == ["Computer Science", "AI"]
        assert result["language"] == "en"
        assert result["metadata_source"] == "semantic_scholar"
        assert result["citation_count"] == 42
        assert result["external_ids"]["SemanticScholar"] == "abc123"

    def test_missing_paper_id_raises_error(self):
        paper = {"title": "Test Paper"}
        with pytest.raises(AcademicPaperImportError) as exc_info:
            _paper_metadata(paper)
        assert exc_info.value.error_type == "paper_metadata_invalid"

    def test_missing_title_raises_error(self):
        paper = {"paperId": "abc123"}
        with pytest.raises(AcademicPaperImportError) as exc_info:
            _paper_metadata(paper)
        assert exc_info.value.error_type == "paper_metadata_invalid"

    def test_empty_title_raises_error(self):
        paper = {"paperId": "abc123", "title": "  "}
        with pytest.raises(AcademicPaperImportError) as exc_info:
            _paper_metadata(paper)
        assert exc_info.value.error_type == "paper_metadata_invalid"

    def test_missing_abstract_returns_none(self):
        paper = {"paperId": "abc", "title": "Test"}
        result = _paper_metadata(paper)
        assert result["abstract"] is None

    def test_authors_with_empty_names_filtered(self):
        paper = {
            "paperId": "abc",
            "title": "Test",
            "authors": [{"name": "Alice"}, {"name": ""}, {"name": "  "}, {"name": "Bob"}],
        }
        result = _paper_metadata(paper)
        assert result["authors"] == ["Alice", "Bob"]

    def test_doi_lowercased(self):
        paper = {
            "paperId": "abc",
            "title": "Test",
            "externalIds": {"DOI": "10.1000/UPPER"},
        }
        result = _paper_metadata(paper)
        assert result["doi"] == "10.1000/upper"

    def test_missing_external_ids_uses_empty_dict(self):
        paper = {"paperId": "abc", "title": "Test"}
        result = _paper_metadata(paper)
        assert result["external_ids"]["SemanticScholar"] == "abc"
        assert result["doi"] is None

    def test_empty_abstract_returns_none(self):
        paper = {"paperId": "abc", "title": "Test", "abstract": ""}
        result = _paper_metadata(paper)
        assert result["abstract"] is None


# ---------------------------------------------------------------------------
# _open_access_url
# ---------------------------------------------------------------------------

class TestOpenAccessUrl:
    def test_valid_open_access_pdf_returns_url(self):
        paper = {"openAccessPdf": {"url": "https://example.com/paper.pdf"}}
        assert _open_access_url(paper) == "https://example.com/paper.pdf"

    def test_missing_open_access_pdf_returns_none(self):
        paper = {"title": "Test"}
        assert _open_access_url(paper) is None

    def test_open_access_pdf_not_dict_returns_none(self):
        paper = {"openAccessPdf": "not a dict"}
        assert _open_access_url(paper) is None

    def test_empty_url_returns_none(self):
        paper = {"openAccessPdf": {"url": ""}}
        assert _open_access_url(paper) is None

    def test_whitespace_url_stripped(self):
        paper = {"openAccessPdf": {"url": "  https://example.com/paper.pdf  "}}
        assert _open_access_url(paper) == "https://example.com/paper.pdf"

    def test_none_url_returns_none(self):
        paper = {"openAccessPdf": {"url": None}}
        assert _open_access_url(paper) is None
