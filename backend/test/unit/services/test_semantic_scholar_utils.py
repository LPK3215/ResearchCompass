"""semantic_scholar_service 纯函数单元测试。

不依赖外部 API 或数据库，只验证标识符识别、归一化和标题比较逻辑。
"""
from __future__ import annotations

import pytest

from yuxi.services.semantic_scholar_service import (
    is_paper_identifier,
    normalize_paper_identifier,
    normalize_paper_title,
)


# ---------------------------------------------------------------------------
# is_paper_identifier
# ---------------------------------------------------------------------------

class TestIsPaperIdentifier:
    def test_doi_returns_true(self):
        assert is_paper_identifier("10.48550/arXiv.1706.03762") is True

    def test_doi_with_short_prefix_returns_true(self):
        assert is_paper_identifier("10.1000/xyz") is True

    def test_arxiv_prefix_returns_true(self):
        assert is_paper_identifier("arxiv:1706.03762") is True

    def test_doi_prefix_returns_true(self):
        assert is_paper_identifier("doi:10.1000/xyz") is True

    def test_corpusid_prefix_returns_true(self):
        assert is_paper_identifier("corpusid:12345") is True

    def test_pmid_prefix_returns_true(self):
        assert is_paper_identifier("pmid:123456") is True

    def test_s2paperid_prefix_returns_true(self):
        assert is_paper_identifier("s2paperid:abc123") is True

    def test_40_char_hex_returns_true(self):
        assert is_paper_identifier("a" * 40) is True

    def test_40_char_hex_uppercase_returns_true(self):
        assert is_paper_identifier("A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2") is True

    def test_plain_title_returns_false(self):
        assert is_paper_identifier("Attention Is All You Need") is False

    def test_empty_string_returns_false(self):
        assert is_paper_identifier("") is False

    def test_short_doi_returns_false(self):
        """DOI 必须以 10. 开头且至少 4 位数字"""
        assert is_paper_identifier("10.1/abc") is False  # only 1 digit, needs 4-9
        assert is_paper_identifier("10.abc/xyz") is False

    def test_none_returns_false(self):
        assert is_paper_identifier(None) is False


# ---------------------------------------------------------------------------
# normalize_paper_identifier
# ---------------------------------------------------------------------------

class TestNormalizePaperIdentifier:
    def test_doi_gets_doi_prefix(self):
        result = normalize_paper_identifier("10.48550/arXiv.1706.03762")
        assert result == "DOI:10.48550/arXiv.1706.03762"

    def test_s2paperid_strips_prefix(self):
        result = normalize_paper_identifier("s2paperid:abc123def456")
        assert result == "abc123def456"

    def test_arxiv_id_kept_as_is(self):
        result = normalize_paper_identifier("ARXIV:1706.03762")
        assert result == "ARXIV:1706.03762"

    def test_corpusid_kept_as_is(self):
        result = normalize_paper_identifier("CorpusId:12345")
        assert result == "CorpusId:12345"

    def test_plain_hex_kept_as_is(self):
        hex_id = "a" * 40
        result = normalize_paper_identifier(hex_id)
        assert result == hex_id

    def test_empty_string_returns_empty(self):
        assert normalize_paper_identifier("") == ""

    def test_whitespace_stripped(self):
        result = normalize_paper_identifier("  10.1000/xyz  ")
        assert result == "DOI:10.1000/xyz"


# ---------------------------------------------------------------------------
# normalize_paper_title
# ---------------------------------------------------------------------------

class TestNormalizePaperTitle:
    def test_casefold_and_collapse_whitespace(self):
        assert normalize_paper_title("Attention  Is  ALL  You  Need") == "attention is all you need"

    def test_punctuation_replaced_with_space(self):
        assert normalize_paper_title("Attention: Is-All-You-Need!") == "attention is all you need"

    def test_chinese_characters_preserved(self):
        assert normalize_paper_title("深度学习：综述") == "深度学习 综述"

    def test_mixed_alphanumeric_preserved(self):
        assert normalize_paper_title("BERT-Base/Uncased") == "bert base uncased"

    def test_empty_string_returns_empty(self):
        assert normalize_paper_title("") == ""

    def test_only_punctuation_returns_empty(self):
        assert normalize_paper_title("---===!!!") == ""
