from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from yuxi.knowledge.chunking.ragflow_like import nlp

ACADEMIC_CHUNK_HARD_LIMIT_RATIO = 1.25

SECTION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("abstract", (r"abstract", r"摘要", r"内容提要")),
    ("introduction", (r"introduction", r"background", r"引言", r"绪论", r"研究背景")),
    (
        "related_work",
        (r"related\s+work", r"literature\s+review", r"prior\s+work", r"相关工作", r"文献综述", r"研究现状"),
    ),
    (
        "methodology",
        (
            r"method(?:ology)?s?",
            r"materials?\s+and\s+methods?",
            r"approach",
            r"proposed\s+method",
            r"方法(?:论)?",
            r"研究方法",
            r"模型",
            r"算法",
        ),
    ),
    (
        "experiments",
        (
            r"experiments?",
            r"evaluation",
            r"results?(?:\s+and\s+discussion)?",
            r"实验(?:结果)?",
            r"结果与讨论",
            r"结果",
            r"评估",
        ),
    ),
    ("discussion", (r"discussion", r"analysis", r"讨论", r"分析")),
    ("conclusion", (r"conclusions?", r"总结", r"结论", r"展望", r"总结与展望")),
    ("limitations", (r"limitations?", r"threats?\s+to\s+validity", r"局限(?:性)?", r"有效性威胁")),
    ("references", (r"references?", r"bibliography", r"参考文献")),
    ("appendix", (r"appendi(?:x|ces)", r"supplementary", r"附录", r"补充材料")),
)

_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_PATTERN = re.compile(r"^\s*(```+|~~~+)")


def _parse_frontmatter(markdown_content: str) -> tuple[dict[str, Any], int]:
    match = re.match(r"^\s*---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", markdown_content, re.DOTALL)
    if not match:
        return {}, 0

    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"论文 frontmatter 不是有效 YAML: {exc}") from exc
    if value is not None and not isinstance(value, dict):
        raise ValueError("论文 frontmatter 必须是键值对象")
    return value or {}, match.end()


def _clean_heading(value: str) -> str:
    text = re.sub(r"^\s*(?:\d+(?:\.\d+)*[.)、]?|[一二三四五六七八九十]+[、.])\s*", "", value or "")
    return re.sub(r"\s+", " ", text).strip(" #\t:：")


def _normalize_authors(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(author).strip() for author in value if str(author).strip()]
    if not isinstance(value, str):
        return []
    return [author.strip() for author in re.split(r"[,;；，、]|\band\b", value) if author.strip()]


def _extract_labeled_value(markdown_content: str, labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?im)^\s*(?:{label_pattern})\s*[:：]\s*(.+?)\s*$", markdown_content[:5000])
    return match.group(1).strip() if match else None


def classify_section(title: str) -> str:
    normalized = _clean_heading(title).lower()
    for section_type, patterns in SECTION_PATTERNS:
        if any(re.fullmatch(pattern, normalized, re.IGNORECASE) for pattern in patterns):
            return section_type
    return "other"


def _infer_title(markdown_content: str, filename: str) -> str:
    for match in re.finditer(r"(?m)^#\s+(.+?)\s*$", markdown_content):
        candidate = _clean_heading(match.group(1))
        if candidate and classify_section(candidate) == "other":
            return candidate
    return Path(filename).stem.strip() or "Untitled paper"


def extract_paper_metadata(
    filename: str,
    markdown_content: str,
    configured_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frontmatter, _ = _parse_frontmatter(markdown_content)
    provided = configured_metadata if isinstance(configured_metadata, dict) else {}

    title_value = provided["title"] if "title" in provided else frontmatter.get("title")
    title = str(title_value or _infer_title(markdown_content, filename)).strip()
    authors_value = provided["authors"] if "authors" in provided else frontmatter.get("authors")
    if authors_value is None:
        authors_value = _extract_labeled_value(markdown_content, ("authors", "author", "作者"))
    authors = _normalize_authors(authors_value)

    if "publication_year" in provided:
        year_value = provided["publication_year"]
    elif "year" in provided:
        year_value = provided["year"]
    else:
        year_value = frontmatter.get("year")
    if year_value is None:
        year_value = _extract_labeled_value(markdown_content, ("year", "published", "publication year", "年份"))
    year_match = _YEAR_PATTERN.search(str(year_value or ""))
    year = int(year_match.group(0)) if year_match else None

    doi_is_provided = "doi" in provided
    doi_value = provided.get("doi") if doi_is_provided else frontmatter.get("doi")
    doi_source = str(doi_value or "") if doi_is_provided else str(doi_value or "") or markdown_content[:5000]
    doi_match = _DOI_PATTERN.search(doi_source)
    doi = doi_match.group(0).rstrip(".,;)").lower() if doi_match else None

    venue_value = provided.get("venue") if "venue" in provided else frontmatter.get("venue")
    venue = str(venue_value or "").strip() or None
    keywords_value = provided.get("keywords") if "keywords" in provided else frontmatter.get("keywords")
    keywords = _normalize_authors(keywords_value)
    identity = doi or f"{title.casefold()}|{year or ''}"
    paper_id = str(provided.get("paper_id") or hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24])
    detected_language = "zh" if len(re.findall(r"[\u4e00-\u9fff]", markdown_content[:4000])) >= 20 else "en"
    language = str(provided.get("language") or detected_language)
    external_ids = provided.get("external_ids")
    if not isinstance(external_ids, dict):
        external_ids = {}
    citation_count = provided.get("citation_count")

    return {
        "paper_id": paper_id,
        "title": title,
        "authors": authors,
        "publication_year": year,
        "venue": venue,
        "doi": doi,
        "keywords": keywords,
        "language": language,
        "external_ids": external_ids,
        "citation_count": citation_count,
        "metadata_source": str(provided.get("metadata_source") or "document"),
        "metadata_status": str(provided.get("metadata_status") or "extracted"),
        "metadata_revision": max(int(provided.get("metadata_revision") or 1), 1),
        "indexed_revision": max(int(provided.get("indexed_revision") or 0), 0),
    }


def _line_offsets(text: str) -> tuple[list[str], list[int]]:
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    if not lines and text:
        return [text], [0]
    return lines, offsets


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _iter_blocks(section_text: str, section_start: int) -> list[tuple[str, int, int, str | None]]:
    lines, offsets = _line_offsets(section_text)
    blocks: list[tuple[str, int, int, str | None]] = []
    index = 0

    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue

        start = index
        element_type = None
        fence_match = _FENCE_PATTERN.match(lines[index])
        if fence_match:
            marker = fence_match.group(1)
            element_type = "code"
            index += 1
            while index < len(lines) and not lines[index].lstrip().startswith(marker):
                index += 1
            index = min(index + 1, len(lines))
        elif lines[index].strip().startswith("$$"):
            element_type = "formula"
            if lines[index].strip().count("$$") >= 2:
                index += 1
            else:
                index += 1
                while index < len(lines) and "$$" not in lines[index].strip():
                    index += 1
                index = min(index + 1, len(lines))
        elif _is_table_line(lines[index]):
            element_type = "table"
            index += 1
            while index < len(lines) and _is_table_line(lines[index]):
                index += 1
        else:
            index += 1
            while index < len(lines) and lines[index].strip():
                if _FENCE_PATTERN.match(lines[index]) or lines[index].strip().startswith("$$"):
                    break
                if _is_table_line(lines[index]):
                    break
                index += 1

        local_start = offsets[start]
        local_end = offsets[index] if index < len(offsets) else len(section_text)
        content = section_text[local_start:local_end].strip()
        if content:
            leading = len(section_text[local_start:local_end]) - len(section_text[local_start:local_end].lstrip())
            trailing = len(section_text[local_start:local_end]) - len(section_text[local_start:local_end].rstrip())
            blocks.append(
                (
                    content,
                    section_start + local_start + leading,
                    section_start + local_end - trailing,
                    element_type,
                )
            )
    return blocks


def _split_sections(markdown_content: str, content_start: int) -> list[dict[str, Any]]:
    lines, offsets = _line_offsets(markdown_content)
    headings: list[tuple[int, int, str]] = []
    fence_marker: str | None = None
    for index, line in enumerate(lines):
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match:
            marker = fence_match.group(1)
            fence_marker = None if fence_marker and marker.startswith(fence_marker[0]) else marker
            continue
        if fence_marker:
            continue
        match = _HEADING_PATTERN.match(line.rstrip("\r\n"))
        if match and offsets[index] >= content_start:
            headings.append((offsets[index], len(match.group(1)), _clean_heading(match.group(2))))

    if not headings:
        return [
            {
                "start": content_start,
                "end": len(markdown_content),
                "level": 1,
                "title": "",
                "path": [],
                "type": "other",
            }
        ]

    sections: list[dict[str, Any]] = []
    title_stack: list[str] = [""] * 6
    if headings[0][0] > content_start:
        sections.append(
            {
                "start": content_start,
                "end": headings[0][0],
                "level": 1,
                "title": "",
                "path": [],
                "type": "other",
            }
        )
    for index, (start, level, title) in enumerate(headings):
        title_stack[level - 1] = title
        for stack_index in range(level, 6):
            title_stack[stack_index] = ""
        sections.append(
            {
                "start": start,
                "end": headings[index + 1][0] if index + 1 < len(headings) else len(markdown_content),
                "level": level,
                "title": title,
                "path": [value for value in title_stack if value],
                "type": classify_section(title),
            }
        )
    return sections


def _split_oversized_block(
    block: tuple[str, int, int, str | None], max_tokens: int
) -> list[tuple[str, int, int, str | None]]:
    content, start, end, element_type = block
    if nlp.count_tokens(content) <= max_tokens or element_type in {"formula", "table", "code"}:
        return [block]

    pieces = nlp.hard_split_by_token_limit(
        content,
        max_tokens,
        hard_limit_token_num=max(max_tokens, int(max_tokens * ACADEMIC_CHUNK_HARD_LIMIT_RATIO)),
    )
    result: list[tuple[str, int, int, str | None]] = []
    search_from = 0
    for piece in pieces:
        found_at = content.find(piece, search_from)
        if found_at < 0:
            raise ValueError("无法将学术分块映射回原文位置")
        piece_start = start + found_at
        piece_end = piece_start + len(piece)
        result.append((piece, piece_start, piece_end, element_type))
        search_from = found_at + len(piece)
    return result


def _paper_context(paper: dict[str, Any], section_label: str) -> str:
    fields = [f"Paper: {paper['title']}"]
    if paper["authors"]:
        fields.append(f"Authors: {', '.join(paper['authors'])}")
    if paper["publication_year"]:
        fields.append(f"Year: {paper['publication_year']}")
    if paper["doi"]:
        fields.append(f"DOI: {paper['doi']}")
    return f"[{' | '.join(fields)}]\n[Academic section: {section_label}]"


def chunk_markdown(
    filename: str,
    markdown_content: str,
    parser_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config = dict(parser_config or {})
    max_tokens = max(int(config.get("chunk_token_num", 512) or 512), 64)
    paper = extract_paper_metadata(filename, markdown_content, config.get("paper_metadata"))
    _, content_start = _parse_frontmatter(markdown_content)
    sections = _split_sections(markdown_content, content_start)
    abstract_section = next((section for section in sections if section["type"] == "abstract"), None)
    configured_metadata = config.get("paper_metadata")
    configured_abstract = configured_metadata.get("abstract") if isinstance(configured_metadata, dict) else None
    if configured_abstract is not None:
        paper["abstract"] = str(configured_abstract).strip() or None
    elif abstract_section:
        abstract_text = markdown_content[abstract_section["start"] : abstract_section["end"]]
        abstract_text = re.sub(r"^#{1,6}\s+.*?(?:\r?\n|$)", "", abstract_text, count=1).strip()
        paper["abstract"] = abstract_text or None
    else:
        paper["abstract"] = None

    records: list[dict[str, Any]] = []
    for section in sections:
        section_text = markdown_content[section["start"] : section["end"]]
        blocks: list[tuple[str, int, int, str | None]] = []
        for block in _iter_blocks(section_text, section["start"]):
            blocks.extend(_split_oversized_block(block, max_tokens))

        current: list[tuple[str, int, int, str | None]] = []
        current_tokens = 0
        for block in blocks:
            block_tokens = nlp.count_tokens(block[0])
            current_types = {item[3] for item in current if item[3]}
            isolate_special_block = bool(current and (block[3] or current_types))
            if current and (isolate_special_block or current_tokens + block_tokens > max_tokens):
                records.append(_build_record(current, paper, section))
                current = []
                current_tokens = 0
            current.append(block)
            current_tokens += block_tokens
            if block[3]:
                records.append(_build_record(current, paper, section))
                current = []
                current_tokens = 0
        if current:
            records.append(_build_record(current, paper, section))

    if not records:
        raise ValueError("论文内容为空，无法生成学术分块")
    return records


def _build_record(
    blocks: list[tuple[str, int, int, str | None]],
    paper: dict[str, Any],
    section: dict[str, Any],
) -> dict[str, Any]:
    raw_content = "\n\n".join(block[0] for block in blocks)
    section_label = section["title"] or section["type"]
    element_types = sorted({block[3] for block in blocks if block[3]})
    return {
        "content": f"{_paper_context(paper, section_label)}\n{raw_content}",
        "source_start": blocks[0][1],
        "source_end": blocks[-1][2],
        "metadata": {
            "document_type": "academic_paper",
            "paper": paper,
            "section_type": section["type"],
            "section_title": section["title"] or None,
            "section_path": section["path"],
            "heading_level": section["level"],
            "element_types": element_types,
        },
    }
