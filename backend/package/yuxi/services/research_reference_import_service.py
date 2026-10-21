"""ResearchCompass 参考文献文件导入服务。

本模块把 BibTeX / RIS 文件解析为论文元数据记录并写入当前知识库。导入的记录只包含
元数据，不创建知识库文件记录，也不参与分块索引；需要全文时再按 DOI 走外部论文导入
链路。解析与去重是纯函数，入库只做一次批量写入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User
from yuxi.utils import hashstr

MAX_REFERENCE_CONTENT_CHARS = 5_000_000
MAX_REFERENCE_ENTRIES = 1000
MAX_TITLE_CHARS = 2000
MAX_VENUE_CHARS = 512
MAX_DOI_CHARS = 512
MAX_AUTHOR_CHARS = 200
MAX_KEYWORD_CHARS = 100
MAX_ABSTRACT_CHARS = 20_000

_YEAR_PATTERN = re.compile(r"(1[5-9]\d{2}|2[01]\d{2})")
_RIS_LINE_PATTERN = re.compile(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$")
_BIBTEX_ENTRY_PATTERN = re.compile(r"@([A-Za-z]+)\s*([{(])")
_BIBTEX_FIELD_PATTERN = re.compile(r"([A-Za-z][\w-]*)\s*=")
_DOI_PREFIX_PATTERN = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_LATEX_COMMAND_PATTERN = re.compile(r"\\[a-zA-Z]+\s*")


class ResearchReferenceImportError(ValueError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


@dataclass(frozen=True)
class ParsedReference:
    title: str
    authors: list[str]
    publication_year: int | None
    venue: str | None
    doi: str | None
    abstract: str | None
    keywords: list[str]


def detect_reference_format(content: str) -> str:
    stripped = content.lstrip()
    if not stripped:
        raise ResearchReferenceImportError("reference_content_empty", "参考文献文件为空")
    if stripped.startswith("@"):
        return "bibtex"
    if any(line.startswith("TY  -") for line in stripped.splitlines()[:20]):
        return "ris"
    raise ResearchReferenceImportError("reference_format_unknown", "无法识别参考文献格式，仅支持 BibTeX 与 RIS")


def parse_references(
    content: str, reference_format: str | None = None
) -> tuple[list[ParsedReference], list[dict[str, Any]]]:
    """解析参考文献文件，返回成功条目与逐条失败原因。"""
    if len(content) > MAX_REFERENCE_CONTENT_CHARS:
        raise ResearchReferenceImportError("reference_content_too_large", "参考文献文件超过大小限制")
    resolved_format = (reference_format or detect_reference_format(content)).strip().lower()
    if resolved_format == "bibtex":
        raw_entries = [_fields for _, _fields in _iter_bibtex_entries(content)]
    elif resolved_format == "ris":
        raw_entries = _iter_ris_entries(content)
    else:
        raise ResearchReferenceImportError("reference_format_unknown", "仅支持 BibTeX 与 RIS 格式")
    if len(raw_entries) > MAX_REFERENCE_ENTRIES:
        raise ResearchReferenceImportError(
            "reference_entries_too_many", f"单次最多导入 {MAX_REFERENCE_ENTRIES} 条参考文献"
        )

    references: list[ParsedReference] = []
    errors: list[dict[str, Any]] = []
    for index, fields in enumerate(raw_entries, start=1):
        try:
            references.append(_build_reference(fields))
        except ResearchReferenceImportError as exc:
            errors.append({"index": index, "error_type": exc.error_type, "message": exc.message})
    if not references and not errors:
        raise ResearchReferenceImportError("reference_entries_empty", "文件中没有可导入的参考文献条目")
    return references, errors


def reference_identity(reference: ParsedReference) -> str:
    if reference.doi:
        return f"doi:{reference.doi}"
    return f"title:{_normalized_title(reference.title)}"


async def import_references(
    *,
    kb_id: str,
    content: str,
    reference_format: str | None,
    current_user: User,
) -> dict[str, Any]:
    """把参考文献文件导入当前知识库，按 DOI 优先、标题兜底去重。"""
    await _ensure_access(current_user, kb_id, write=True)
    references, errors = parse_references(content, reference_format)

    repo = AcademicPaperRepository()
    existing_keys: set[str] = set()
    async for batch in repo.iter_export_batches(kb_id=kb_id, include_archived=True):
        for paper in batch:
            key = _paper_identity(paper)
            if key:
                existing_keys.add(key)

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for reference in references:
        identity = reference_identity(reference)
        if identity in existing_keys:
            skipped.append({"title": reference.title, "reason": "already_exists"})
            continue
        existing_keys.add(identity)
        rows.append(
            {
                "paper_id": f"ref_{hashstr(f'{kb_id}:{identity}', 24)}",
                "file_id": None,
                "title": reference.title,
                "abstract": reference.abstract,
                "authors": reference.authors,
                "publication_year": reference.publication_year,
                "venue": reference.venue,
                "doi": reference.doi,
                "keywords": reference.keywords,
                "external_ids": {"DOI": reference.doi} if reference.doi else {},
                "metadata_source": "reference_file",
                "metadata_status": "extracted",
                "metadata_revision": 1,
                "indexed_revision": 0,
            }
        )

    created, conflicted = await repo.create_metadata_only_batch(kb_id=kb_id, rows=rows)
    return {
        "imported_count": len(created),
        "skipped_count": len(skipped) + conflicted,
        "failed_count": len(errors),
        "skipped": skipped[:50],
        "failed": errors[:50],
        "message": "参考文献已导入，仅含元数据，不参与全文检索；需要全文请按 DOI 导入外部论文",
    }


def _build_reference(fields: dict[str, str]) -> ParsedReference:
    title = _clean_text(fields.get("title", ""))[:MAX_TITLE_CHARS]
    if not title:
        raise ResearchReferenceImportError("reference_title_missing", "参考文献缺少标题")
    venue = fields.get("venue") or fields.get("journal") or fields.get("booktitle") or ""
    return ParsedReference(
        title=title,
        authors=_split_authors(fields.get("authors") or fields.get("author", "")),
        publication_year=_parse_year(fields.get("year") or fields.get("date", "")),
        venue=_clean_text(venue)[:MAX_VENUE_CHARS] or None,
        doi=_normalize_doi(fields.get("doi", "")),
        abstract=_clean_text(fields.get("abstract") or fields.get("summary", ""))[:MAX_ABSTRACT_CHARS] or None,
        keywords=_split_keywords(fields.get("keywords") or fields.get("keyword", "")),
    )


def _iter_bibtex_entries(text: str) -> list[tuple[str, dict[str, str]]]:
    entries: list[tuple[str, dict[str, str]]] = []
    index = 0
    while True:
        at = text.find("@", index)
        if at < 0:
            return entries
        header = _BIBTEX_ENTRY_PATTERN.match(text, at)
        if header is None:
            index = at + 1
            continue
        entry_type = header.group(1).lower()
        opener = header.group(2)
        closer = "}" if opener == "{" else ")"
        try:
            body, index = _read_balanced(text, header.start(2), opener, closer)
        except ResearchReferenceImportError:
            index = header.end()
            continue
        if entry_type not in {"comment", "string", "preamble"}:
            entries.append((entry_type, _parse_bibtex_fields(body)))
    return entries


def _parse_bibtex_fields(body: str) -> dict[str, str]:
    _, separator, rest = body.partition(",")
    if not separator:
        return {}
    fields: dict[str, str] = {}
    index = 0
    while index < len(rest):
        while index < len(rest) and rest[index] in " \t\r\n,":
            index += 1
        field = _BIBTEX_FIELD_PATTERN.match(rest, index)
        if field is None:
            break
        value, index = _read_bibtex_value(rest, field.end())
        fields[field.group(1).lower()] = value
    return fields


def _read_bibtex_value(text: str, start: int) -> tuple[str, int]:
    parts: list[str] = []
    index = start
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        if text[index] == "{":
            value, index = _read_balanced(text, index, "{", "}")
            parts.append(value)
        elif text[index] == '"':
            value, index = _read_quoted(text, index)
            parts.append(value)
        else:
            end = index
            while end < len(text) and text[end] not in ",#":
                end += 1
            parts.append(text[index:end].strip())
            index = end
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == "#":
            index += 1
            continue
        break
    return "".join(parts).strip(), index


def _read_balanced(text: str, start: int, opener: str, closer: str) -> tuple[str, int]:
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    raise ResearchReferenceImportError("reference_syntax_error", "参考文献条目括号不匹配")


def _read_quoted(text: str, start: int) -> tuple[str, int]:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return text[start + 1 : index], index + 1
        index += 1
    raise ResearchReferenceImportError("reference_syntax_error", "参考文献引号不匹配")


def _iter_ris_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, list[str]] = {}
    last_tag: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = _RIS_LINE_PATTERN.match(line)
        if match is not None:
            tag, value = match.group(1), match.group(2).strip()
            if tag == "TY":
                current, last_tag = {}, None
                continue
            if tag == "ER":
                if current:
                    entries.append(_ris_fields(current))
                current, last_tag = {}, None
                continue
            current.setdefault(tag, []).append(value)
            last_tag = tag
            continue
        if last_tag and line.startswith(" ") and current.get(last_tag):
            current[last_tag][-1] = f"{current[last_tag][-1]} {line.strip()}".strip()
    if current:
        entries.append(_ris_fields(current))
    return entries


def _ris_fields(record: dict[str, list[str]]) -> dict[str, str]:
    def first(*tags: str) -> str:
        for tag in tags:
            values = record.get(tag)
            if values and values[0]:
                return values[0]
        return ""

    return {
        "title": first("TI", "T1"),
        "authors": " and ".join(record.get("AU") or record.get("A1") or []),
        "year": first("PY", "Y1"),
        "venue": first("JO", "JF", "T2"),
        "doi": first("DO"),
        "abstract": first("AB", "N2"),
        "keywords": "; ".join(record.get("KW") or []),
    }


def _clean_text(value: str) -> str:
    cleaned = value.replace("\\&", "&").replace("~", " ")
    cleaned = _LATEX_COMMAND_PATTERN.sub("", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _split_authors(value: str) -> list[str]:
    authors: list[str] = []
    for part in re.split(r"\s+and\s+", value, flags=re.IGNORECASE):
        name = _clean_text(part)
        if not name:
            continue
        if name.count(",") == 1:
            last, first = (item.strip() for item in name.split(",", 1))
            name = f"{first} {last}".strip()
        authors.append(name[:MAX_AUTHOR_CHARS])
    return authors


def _split_keywords(value: str) -> list[str]:
    keywords: list[str] = []
    for part in re.split(r"[;,]", value):
        keyword = _clean_text(part)
        if keyword and keyword not in keywords:
            keywords.append(keyword[:MAX_KEYWORD_CHARS])
    return keywords


def _parse_year(value: str) -> int | None:
    match = _YEAR_PATTERN.search(value or "")
    if match is None:
        return None
    year = int(match.group(1))
    return year if 1500 <= year <= datetime.now(UTC).year + 1 else None


def _normalize_doi(value: str) -> str | None:
    doi = _DOI_PREFIX_PATTERN.sub("", (value or "").strip())
    doi = doi[:MAX_DOI_CHARS].strip().casefold()
    return doi or None


def _normalized_title(title: str) -> str:
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (title or "").lower())
    return normalized[:80]


def _paper_identity(paper: Any) -> str:
    doi = str(getattr(paper, "doi", "") or "").strip().casefold()
    if doi:
        return f"doi:{doi}"
    title = _normalized_title(str(getattr(paper, "title", "") or ""))
    return f"title:{title}" if title else ""
