"""Deterministic behavior tests for semantic Markdown chunking."""

from yuxi.knowledge.chunking.ragflow_like.parsers import semantic
from yuxi.knowledge.chunking.ragflow_like.utils.md_parser_utils import infer_heading_level


def _unexpected_embedding_request(_: list[str]) -> None:
    raise AssertionError("Short Markdown should not require an embedding request.")


def test_chunk_markdown_preserves_heading_lineage_and_tables():
    chunks = semantic.chunk_markdown(
        """
# Research Report

## Evidence Review

The evidence review keeps claims tied to source papers.

## Corpus Summary

| Paper | Year |
| --- | --- |
| Reliable Retrieval | 2026 |
""",
        parser_config={"chunk_token_num": 500},
        embed_fn=_unexpected_embedding_request,
    )

    evidence_chunk = next(chunk for chunk in chunks if "claims tied to source papers" in chunk)
    table_chunk = next(chunk for chunk in chunks if "Reliable Retrieval" in chunk)

    assert "Research Report|Evidence Review" in evidence_chunk
    assert "Research Report|Corpus Summary|Table" in table_chunk
    assert "| Paper | Year |" in table_chunk


def test_heading_inference():
    assert infer_heading_level("1. Introduction") == 1
    assert infer_heading_level("1.1 Detailed Design") == 2
    assert infer_heading_level("1.2.3 Core Logic") == 3
    assert infer_heading_level("I. Background") == 1
    assert infer_heading_level("Plain text") == 1
