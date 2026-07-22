from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url


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


def _search_rects(page, query: str, *, max_hits: int = 8) -> list[dict[str, float | int]]:
    """Return page-local rects for a query using native PDF text search."""
    query = re.sub(r"[`*_~|]", " ", query or "")
    query = re.sub(r"\s+", " ", query).strip()
    if len(query) < 8:
        return []
    # Search shorter prefixes as well: parsed Markdown can insert line breaks,
    # table syntax, or other layout markers that are absent from the PDF text layer.
    words = query.split()
    candidates = [
        " ".join(words[:count])
        for count in (18, 12, 8)
        if len(words) >= count
    ]
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

    页码与矩形坐标均来自 PyMuPDF 对原始 PDF 文本层的检索结果；
    锚点匹配失败时不写入任何页码或坐标，保持字符区间定位，绝不伪造高亮框。
    """
    if not source_path:
        return chunks
    source_suffix = (
        Path(parse_minio_url(source_path)[1]).suffix
        if is_minio_url(source_path)
        else Path(source_path).suffix
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
            anchor_length = min(80, len(raw_text))
            first_anchor = raw_text[:anchor_length]
            last_anchor = raw_text[-anchor_length:]
            start_page = _find_anchor_page(page_texts, first_anchor)
            if start_page is None:
                continue
            end_page = _find_anchor_page(page_texts, last_anchor, start_page - 1)
            if end_page is None or end_page < start_page:
                continue

            page = document[start_page - 1]
            original_first = _raw_chunk_text(str(chunk.get("content") or ""))
            original_first = re.sub(r"\s+", " ", original_first).strip()
            rects = _search_rects(page, original_first[:120])
            metadata["source_page_start"] = start_page
            metadata["source_page_end"] = end_page
            if rects:
                metadata["source_rects"] = [{"page": start_page, **rect} for rect in rects]
                metadata["locator_type"] = "pdf_page_rect"
            else:
                metadata.pop("source_rects", None)
                metadata["locator_type"] = "pdf_page_range"
    finally:
        document.close()
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
    return chunks


__all__ = ["attach_pdf_page_ranges"]
