"""PDF 页码定位：锚点回退与“只确认起始页”的降级语义。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
for import_path in (BACKEND_ROOT, BACKEND_ROOT / "package"):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from yuxi.knowledge.chunking.pdf_locator import attach_pdf_page_ranges  # noqa: E402

PAGE_ONE = (
    "Retrieval augmented generation combines a dense retriever with a generator "
    "to ground answers in retrieved documents for knowledge intensive tasks."
)
PAGE_TWO = (
    "Evaluation of retrieval augmented generation requires measuring faithfulness "
    "of the generated answer against the retrieved evidence passages."
)
PAGE_THREE = "Figure 2: The overall architecture of the proposed model with three stages."
TABLE_CHUNK = (
    f"| Encoder | Decoder |\n| Multi-Head Attention | Multi-Head Attention |\n| Add & Norm | Add & Norm |\n{PAGE_THREE}"
)


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path_factory.mktemp("pdf") / "paper.pdf"
    document = fitz.open()
    for text in (PAGE_ONE, PAGE_TWO, PAGE_THREE):
        page = document.new_page()
        # insert_text 会按页宽裁剪长句，导致文本层缺字；用文本框换行才能保留完整句子。
        page.insert_textbox(fitz.Rect(72, 72, 540, 720), text, fontsize=11)
    document.save(pdf_path)
    document.close()
    return pdf_path


def _chunk(content: str) -> dict:
    return {
        "content": f"[Paper: Sample Paper\n[Academic section: Introduction]\n{content}",
        "chunk_metadata": {"document_type": "academic_paper", "section_title": "Introduction"},
    }


def test_verbatim_chunk_gets_page_range(sample_pdf: Path) -> None:
    chunks = [_chunk(PAGE_ONE)]

    attach_pdf_page_ranges(chunks, str(sample_pdf))

    metadata = chunks[0]["chunk_metadata"]
    assert metadata["source_page_start"] == 1
    assert metadata["source_page_end"] == 1
    assert metadata["locator_type"] in {"pdf_page_range", "pdf_page_rect"}


def test_chunk_on_second_page_keeps_page_two(sample_pdf: Path) -> None:
    chunks = [_chunk(PAGE_TWO)]

    attach_pdf_page_ranges(chunks, str(sample_pdf))

    metadata = chunks[0]["chunk_metadata"]
    assert metadata["source_page_start"] == 2
    assert metadata["source_page_end"] == 2


def test_punctuation_difference_still_matches_page(sample_pdf: Path) -> None:
    """解析文本与 PDF 文本层的引号/连字符差异不应让页码丢失。"""
    noisy = PAGE_ONE.replace("tasks.", "tasks.").replace("dense retriever", "dense-retriever")
    chunks = [_chunk(noisy)]

    attach_pdf_page_ranges(chunks, str(sample_pdf))

    metadata = chunks[0]["chunk_metadata"]
    assert metadata["source_page_start"] == 1


def test_unverified_end_keeps_start_page_only(sample_pdf: Path) -> None:
    """结束锚点无法在原文验证时只保留起始页，不猜测区间。"""
    chunks = [_chunk(f"{PAGE_ONE} 这一段补充说明只存在于解析结果中，不在 PDF 文本层里出现。")]

    attach_pdf_page_ranges(chunks, str(sample_pdf))

    metadata = chunks[0]["chunk_metadata"]
    assert metadata["source_page_start"] == 1
    assert "source_page_end" not in metadata
    # 起点已验证且能定位到精确文本框时，仍然不写未经证实的结束页。
    assert metadata["locator_type"] == "pdf_page_rect"


def test_start_only_locator_when_rect_missing(sample_pdf: Path) -> None:
    """起点可验证但找不到精确文本框时，退化为“仅起始页”定位。"""
    noisy = PAGE_ONE.replace("generation", "generat-ion")
    chunks = [_chunk(f"{noisy} 这一段补充说明只存在于解析结果中，不在 PDF 文本层里出现。")]

    attach_pdf_page_ranges(chunks, str(sample_pdf))

    metadata = chunks[0]["chunk_metadata"]
    assert metadata["source_page_start"] == 1
    assert "source_page_end" not in metadata
    assert metadata["locator_type"] == "pdf_page_start"


def test_unmatched_chunk_keeps_character_range(sample_pdf: Path) -> None:
    chunks = [_chunk("完全无关的内容，不会出现在 PDF 文本层中，因此不应被标注任何页码。")]

    attach_pdf_page_ranges(chunks, str(sample_pdf))

    metadata = chunks[0]["chunk_metadata"]
    assert "source_page_start" not in metadata
    assert "source_page_end" not in metadata
    assert metadata["locator_type"] == "parsed_source_character_range"


def test_table_chunk_falls_back_to_inner_fragment(sample_pdf: Path) -> None:
    """图表 chunk 开头是表格标记时，用内部可命中的图注定位页面。"""
    chunks = [_chunk(TABLE_CHUNK)]

    attach_pdf_page_ranges(chunks, str(sample_pdf))

    metadata = chunks[0]["chunk_metadata"]
    assert metadata["source_page_start"] == 3
    assert metadata["locator_type"] == "pdf_page_range"


def test_non_pdf_source_is_skipped(tmp_path: Path) -> None:
    markdown = tmp_path / "paper.md"
    markdown.write_text("# title", encoding="utf-8")
    chunks = [_chunk(PAGE_ONE)]

    attach_pdf_page_ranges(chunks, str(markdown))

    metadata = chunks[0]["chunk_metadata"]
    assert "source_page_start" not in metadata
    assert "locator_type" not in metadata
