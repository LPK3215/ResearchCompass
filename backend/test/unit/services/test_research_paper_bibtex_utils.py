"""research_paper_service BibTeX 导出纯函数单元测试。

不依赖外部服务，只验证 BibTeX 转义、键生成、作者格式和完整条目输出。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.services.research_paper_service import (
    _bibtex_key,
    _escape_bibtex,
    _format_bibtex_authors,
    _paper_to_bibtex,
)


# ---------------------------------------------------------------------------
# _escape_bibtex
# ---------------------------------------------------------------------------

class TestEscapeBibtex:
    def test_plain_text_unchanged(self):
        assert _escape_bibtex("Hello World") == "Hello World"

    def test_ampersand_escaped(self):
        assert _escape_bibtex("A & B") == r"A \& B"

    def test_underscore_escaped(self):
        assert _escape_bibtex("foo_bar") == r"foo\_bar"

    def test_percent_escaped(self):
        assert _escape_bibtex("100%") == r"100\%"

    def test_all_special_chars_escaped(self):
        assert _escape_bibtex("a&b_c%d") == r"a\&b\_c\%d"

    def test_none_returns_empty_string(self):
        assert _escape_bibtex(None) == ""

    def test_empty_string_returns_empty(self):
        assert _escape_bibtex("") == ""

    def test_non_string_coerced_to_string(self):
        assert _escape_bibtex(123) == "123"


# ---------------------------------------------------------------------------
# _bibtex_key
# ---------------------------------------------------------------------------

def _make_paper(**kwargs):
    defaults = {
        "authors": ["John Doe"],
        "publication_year": 2023,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestBibtexKey:
    def test_basic_key_generation(self):
        paper = _make_paper()
        assert _bibtex_key(paper, 1) == "Doe20231"

    def test_multi_word_surname_uses_last_word(self):
        paper = _make_paper(authors=["Alice van der Berg"])
        assert _bibtex_key(paper, 2) == "Berg20232"

    def test_no_authors_uses_anon(self):
        paper = _make_paper(authors=[])
        assert _bibtex_key(paper, 1) == "anon20231"

    def test_empty_authors_list_uses_anon(self):
        paper = _make_paper(authors=[""])
        assert _bibtex_key(paper, 3) == "anon20233"

    def test_no_publication_year_uses_nd(self):
        paper = _make_paper(publication_year=None)
        assert _bibtex_key(paper, 1) == "Doend1"

    def test_index_appended(self):
        paper = _make_paper()
        assert _bibtex_key(paper, 5) == "Doe20235"


# ---------------------------------------------------------------------------
# _format_bibtex_authors
# ---------------------------------------------------------------------------

class TestFormatBibtexAuthors:
    def test_empty_list_returns_empty(self):
        assert _format_bibtex_authors([]) == ""

    def test_single_author(self):
        assert _format_bibtex_authors(["John Doe"]) == "John Doe"

    def test_two_authors_joined_with_and(self):
        result = _format_bibtex_authors(["Alice", "Bob"])
        assert result == "Alice and Bob"

    def test_three_authors_joined_with_and(self):
        result = _format_bibtex_authors(["Alice", "Bob", "Charlie"])
        assert result == "Alice and Bob and Charlie"


# ---------------------------------------------------------------------------
# _paper_to_bibtex
# ---------------------------------------------------------------------------

class TestPaperToBibtex:
    def test_minimal_paper_with_title_only(self):
        paper = SimpleNamespace(
            title="Test Paper",
            authors=[],
            publication_year=None,
            venue=None,
            doi=None,
            abstract=None,
            keywords=[],
        )
        result = _paper_to_bibtex(paper, 1)
        assert result.startswith("@article{anonnd1,")
        assert "title = {Test Paper}" in result
        assert result.rstrip().endswith("}")

    def test_full_paper_includes_all_fields(self):
        paper = SimpleNamespace(
            title="Attention Is All You Need",
            authors=["Ashish Vaswani", "Noam Shazeer"],
            publication_year=2017,
            venue="NeurIPS",
            doi="10.1234/test",
            abstract="We propose a new architecture...",
            keywords=["transformer", "attention"],
        )
        result = _paper_to_bibtex(paper, 1)
        assert "@article{Vaswani20171," in result
        assert "title = {Attention Is All You Need}" in result
        assert "author = {Ashish Vaswani and Noam Shazeer}" in result
        assert "year = {2017}" in result
        assert "journal = {NeurIPS}" in result
        assert "doi = {10.1234/test}" in result
        assert "abstract = {We propose a new architecture...}" in result
        assert "keywords = {transformer, attention}" in result

    def test_special_chars_in_title_escaped(self):
        paper = SimpleNamespace(
            title="A&B_C%D",
            authors=[],
            publication_year=None,
            venue=None,
            doi=None,
            abstract=None,
            keywords=[],
        )
        result = _paper_to_bibtex(paper, 1)
        assert r"title = {A\&B\_C\%D}" in result

    def test_doi_with_underscore_escaped(self):
        paper = SimpleNamespace(
            title="Test",
            authors=[],
            publication_year=None,
            venue=None,
            doi="10.1234/test_paper",
            abstract=None,
            keywords=[],
        )
        result = _paper_to_bibtex(paper, 1)
        assert r"doi = {10.1234/test\_paper}" in result
