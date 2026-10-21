from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url

# 锚点按长度递减尝试：解析出的 Markdown 与 PDF 文本层常有断行、连字符、标点差异，
# 长锚点匹配失败时用更短的词对齐锚点重试，仍然只接受真实命中原文文本层的锚点。
ANCHOR_LENGTHS = (80, 60, 45, 30, 20)
MIN_ANCHOR_LENGTH = 12

# 起点锚点完全命中不了时（图表 chunk 开头是表格标记，PDF 文本层里没有），
# 在 chunk 内部向前扫描一段连续文本，用它定位所在页面。
SCAN_STEP = 20
SCAN_WINDOW = 80
SCAN_MIN_MATCH = 45

# 长锚点可以用文档顺序消歧（同一页在多页重复时取靠后的一页）；
# 短锚点区分度不足，必须在全文唯一命中才采信。
STRONG_ANCHOR_LENGTH = 45


def _normalize(value: str) -> str:
    value = re.sub(r"(?m)^#{1,6}\s*", "", value or "")
    value = re.sub(r"-\s+", "", value)
    value = re.sub(r"[`*_~]", "", value)
    return re.sub(r"\s+", " ", value).strip().casefold()


def _raw_chunk_text(content: str) -> str:
    lines = (content or "").splitlines()
    if len(lines) >= 3 and lines[0].startswith("[Paper:") and lines[1].startswith("[Academic section:"):
        lines = lines[2:]
    lines = [line for line in lines if not re.match(r"^\s*#{1,6}\s+", line)]
    return "\n".join(lines).strip()


def _find_anchor_page(page_texts: list[str], anchor: str, start: int = 0) -> int | None:
    if not anchor:
        return None
    for page_index in range(max(start, 0), len(page_texts)):
        if anchor in page_texts[page_index]:
            return page_index + 1
    return None


def _find_unique_anchor_page(page_texts: list[str], anchor: str) -> int | None:
    """只在全文唯一命中时返回页码，避免重复段落造成误判。"""
    if not anchor:
        return None
    hits = [index + 1 for index, text in enumerate(page_texts) if anchor in text]
    return hits[0] if len(hits) == 1 else None


def _loose_text(value: str) -> str:
    """只保留字母数字，忽略标点与空白差异，用于容忍排版差异。"""
    return re.sub(r"[^0-9a-z]+", "", value.casefold())


def _anchor_variants(text: str, *, suffix: bool) -> list[str]:
    """按长度递减生成锚点，边界对齐到词首/词尾，避免半个词。"""
    variants: list[str] = []
    for length in ANCHOR_LENGTHS:
        if len(text) <= length:
            continue
        piece = text[-length:] if suffix else text[:length]
        if suffix:
            cut = piece.find(" ")
            if cut > 0:
                piece = piece[cut + 1 :]
        else:
            cut = piece.rfind(" ")
            if cut >= length // 2:
                piece = piece[:cut]
        if len(piece) >= MIN_ANCHOR_LENGTH:
            variants.append(piece)
    return variants


def _loose_anchor(anchor: str) -> str | None:
    """宽松锚点：忽略标点与空白差异；含非 ASCII 字符时不用，避免丢失正文语义。"""
    if not anchor.isascii():
        return None
    loose_anchor = _loose_text(anchor)
    return loose_anchor if len(loose_anchor) >= MIN_ANCHOR_LENGTH else None


def _find_loose_page(loose_page_texts: list[str], anchor: str, start: int = 1) -> int | None:
    """忽略标点的匹配，用于容忍解析文本与 PDF 文本层的排版差异。"""
    loose_anchor = _loose_anchor(anchor)
    if loose_anchor is None:
        return None
    for page_index in range(max(start - 1, 0), len(loose_page_texts)):
        if loose_anchor in loose_page_texts[page_index]:
            return page_index + 1
    return None


def _find_unique_loose_page(loose_page_texts: list[str], anchor: str) -> int | None:
    loose_anchor = _loose_anchor(anchor)
    if loose_anchor is None:
        return None
    hits = [index + 1 for index, text in enumerate(loose_page_texts) if loose_anchor in text]
    return hits[0] if len(hits) == 1 else None


def _find_start_page(page_texts: list[str], loose_page_texts: list[str], text: str, last_page: int) -> int | None:
    """定位 chunk 起始页：严格匹配优先，其次忽略标点的宽松匹配，最后回退到内部扫描。"""
    for anchor in _anchor_variants(text, suffix=False):
        if len(anchor) >= STRONG_ANCHOR_LENGTH:
            page = _find_anchor_page(page_texts, anchor, start=max(last_page - 1, 0))
            if page is not None:
                return page
        page = _find_unique_anchor_page(page_texts, anchor)
        if page is not None:
            return page
    for anchor in _anchor_variants(text, suffix=False):
        if len(anchor) >= STRONG_ANCHOR_LENGTH:
            page = _find_loose_page(loose_page_texts, anchor, start=max(last_page, 1))
            if page is not None:
                return page
        page = _find_unique_loose_page(loose_page_texts, anchor)
        if page is not None:
            return page
    return _find_scanned_page(page_texts, loose_page_texts, text)


def _find_scanned_page(page_texts: list[str], loose_page_texts: list[str], text: str) -> int | None:
    """起点锚点命中不了时，在 chunk 内部向前扫描可唯一定位的连续文本。

    图表、公式被解析成表格时，chunk 开头是表格标记，在 PDF 文本层里原样找不到；
    但其中的图注或正文片段可以命中，用它定位页面比完全丢掉页码更可靠。
    """
    for offset in range(SCAN_STEP, max(len(text) - SCAN_MIN_MATCH, 0), SCAN_STEP):
        window = text[offset : offset + SCAN_WINDOW]
        for anchor in _anchor_variants(window, suffix=False):
            if len(anchor) < SCAN_MIN_MATCH:
                continue
            page = _find_unique_anchor_page(page_texts, anchor)
            if page is not None:
                return page
        for anchor in _anchor_variants(window, suffix=False):
            if len(anchor) < SCAN_MIN_MATCH:
                continue
            page = _find_unique_loose_page(loose_page_texts, anchor)
            if page is not None:
                return page
    return None


def _find_end_page(page_texts: list[str], loose_page_texts: list[str], text: str, start_page: int) -> int | None:
    """定位 chunk 结束页；无法在原文验证时返回 None，此时只保留起始页。"""
    for anchor in _anchor_variants(text, suffix=True):
        if len(anchor) >= STRONG_ANCHOR_LENGTH:
            page = _find_anchor_page(page_texts, anchor, start=start_page - 1)
            if page is not None:
                return page
        page = _find_unique_anchor_page(page_texts, anchor)
        if page is not None:
            return page
    for anchor in _anchor_variants(text, suffix=True):
        if len(anchor) >= STRONG_ANCHOR_LENGTH:
            page = _find_loose_page(loose_page_texts, anchor, start=start_page)
            if page is not None:
                return page
        page = _find_unique_loose_page(loose_page_texts, anchor)
        if page is not None:
            return page
    return None


def _search_rects(page, query: str, *, max_hits: int = 8) -> list[dict[str, float | int]]:
    """Return page-local rects for a query using native PDF text search."""
    query = re.sub(r"[`*_~|]", " ", query or "")
    query = re.sub(r"\s+", " ", query).strip()
    if len(query) < 8:
        return []
    # Search shorter prefixes as well: parsed Markdown can insert line breaks,
    # table syntax, or other layout markers that are absent from the PDF text layer.
    words = query.split()
    candidates = [" ".join(words[:count]) for count in (18, 12, 8) if len(words) >= count]
    candidates.extend((query[:120], query[:80], query[:40]))
    candidates = list(dict.fromkeys(candidate for candidate in candidates if len(candidate) >= 8))
    seen: set[tuple[float, float, float, float]] = set()
    rects: list[dict[str, float | int]] = []
    page_width = float(page.cropbox.width) or 1.0
    page_height = float(page.cropbox.height) or 1.0
    page_rotation = int(page.rotation or 0) % 360
    for candidate in candidates:
        try:
            hits = page.search_for(candidate, quads=False)
        except Exception:
            hits = []
        for rect in hits:
            key = (
                round(float(rect.x0), 2),
                round(float(rect.y0), 2),
                round(float(rect.x1), 2),
                round(float(rect.y1), 2),
            )
            if key in seen:
                continue
            seen.add(key)
            x0 = min(max(float(rect.x0) / page_width, 0.0), 1.0)
            y0 = min(max(float(rect.y0) / page_height, 0.0), 1.0)
            x1 = min(max(float(rect.x1) / page_width, 0.0), 1.0)
            y1 = min(max(float(rect.y1) / page_height, 0.0), 1.0)
            if x1 <= x0 or y1 <= y0:
                continue
            rects.append(
                {
                    "x0": float(rect.x0),
                    "y0": float(rect.y0),
                    "x1": float(rect.x1),
                    "y1": float(rect.y1),
                    "nx0": x0,
                    "ny0": y0,
                    "nx1": x1,
                    "ny1": y1,
                    "rotation": page_rotation,
                }
            )
            if len(rects) >= max_hits:
                return rects
        if rects:
            return rects
    return rects


def attach_pdf_page_ranges(chunks: list[dict[str, Any]], source_path: str | None) -> list[dict[str, Any]]:
    """为可匹配到原始 PDF 文本的学术 chunk 增加真实页码与坐标范围。

    页码与矩形坐标均来自 PyMuPDF 对原始 PDF 文本层的检索结果；起始页只有在锚点
    真实命中原文文本层时才写入（起点匹配不到时回退到 chunk 内部的片段扫描），
    结束页无法验证时只保留起始页，不猜测区间；完全匹配不上时保持字符区间定位，
    绝不伪造高亮框。
    """
    if not source_path:
        return chunks
    source_suffix = (
        Path(parse_minio_url(source_path)[1]).suffix if is_minio_url(source_path) else Path(source_path).suffix
    )
    if source_suffix.casefold() != ".pdf":
        return chunks

    temp_path: str | None = None
    try:
        import fitz

        if is_minio_url(source_path):
            from yuxi.storage.minio import get_minio_client

            bucket_name, object_name = parse_minio_url(source_path)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
                temp_path = temp_file.name
                temp_file.write(get_minio_client().download_file(bucket_name, object_name))
            document = fitz.open(temp_path)
        else:
            document = fitz.open(source_path)
    except Exception as exc:
        raise RuntimeError(f"无法打开原始 PDF 以建立页码/坐标定位: {exc}") from exc

    try:
        page_texts = [_normalize(page.get_text("text")) for page in document]
        if not any(page_texts):
            return chunks
        loose_page_texts = [_loose_text(text) for text in page_texts]

        last_page = 0
        for chunk in chunks:
            metadata = chunk.get("chunk_metadata")
            if not isinstance(metadata, dict) or metadata.get("document_type") != "academic_paper":
                continue
            for key in ("source_page_start", "source_page_end", "source_rects"):
                metadata.pop(key, None)
            metadata["locator_type"] = "parsed_source_character_range"
            raw_text = _normalize(_raw_chunk_text(str(chunk.get("content") or "")))
            if len(raw_text) < 20:
                continue
            start_page = _find_start_page(page_texts, loose_page_texts, raw_text, last_page)
            if start_page is None:
                continue
            last_page = start_page
            end_page = _find_end_page(page_texts, loose_page_texts, raw_text, start_page)
            metadata["source_page_start"] = start_page
            if end_page is None or end_page < start_page:
                metadata.pop("source_page_end", None)
            else:
                metadata["source_page_end"] = end_page
            page = document[start_page - 1]
            original_first = _raw_chunk_text(str(chunk.get("content") or ""))
            original_first = re.sub(r"\s+", " ", original_first).strip()
            rects = _search_rects(page, original_first[:120])
            if rects:
                metadata["source_rects"] = [{"page": start_page, **rect} for rect in rects]
                metadata["locator_type"] = "pdf_page_rect"
            elif end_page is not None:
                metadata["locator_type"] = "pdf_page_range"
            else:
                metadata["locator_type"] = "pdf_page_start"
    finally:
        document.close()
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
    return chunks


__all__ = ["attach_pdf_page_ranges"]
