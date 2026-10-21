"""ResearchCompass 论文元数据补全服务。

本模块是本仓库在开源智能体框架 Yuxi 之上实现的论文元数据补全业务：按 DOI（优先）或
标题从 Crossref 回填论文缺失的 DOI、摘要、作者、年份、期刊、关键词与引用数；Crossref
没有命中时再用 Semantic Scholar 的严格标题+年份匹配兜底。补全只写空字段，不覆盖人工
校正值；有原文文件的论文沿用"元数据修订 → 重新切分索引"流程，只有元数据的记录直接
更新字段。Crossref / Semantic Scholar 客户端只做外部数据适配，不参与业务决策。
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.services.research_paper_service import (
    _enqueue_paper_reindex,
    _ensure_access,
    _serialize_paper,
)
from yuxi.services.semantic_scholar_service import (
    SemanticScholarClient,
    SemanticScholarError,
    normalize_paper_title,
)
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger

FILLABLE_FIELDS = ("doi", "abstract", "authors", "publication_year", "venue", "keywords", "citation_count")
FIELD_LABELS = {
    "doi": "DOI",
    "abstract": "摘要",
    "authors": "作者",
    "publication_year": "年份",
    "venue": "期刊或会议",
    "keywords": "关键词",
    "citation_count": "引用数",
}
MAX_DOI_CHARS = 512
MAX_VENUE_CHARS = 512
MAX_ABSTRACT_CHARS = 20_000
MAX_AUTHOR_CHARS = 200
MAX_KEYWORD_CHARS = 100
CROSSREF_SEARCH_ROWS = 5
# Semantic Scholar 是兜底数据源，单次补全不让它拖住 HTTP 请求。
SEMANTIC_SCHOLAR_TIMEOUT_SECONDS = 20.0

_JATS_TAG_PATTERN = re.compile(r"<[^>]+>")


class ResearchPaperMetadataError(RuntimeError):
    """论文元数据补全的可预期失败。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def missing_paper_fields(paper: Any) -> list[str]:
    """列出论文当前为空的元数据字段；只把 None/空串/空列表视为缺失。"""
    missing: list[str] = []
    if not str(getattr(paper, "doi", "") or "").strip():
        missing.append("doi")
    if not _plain_text(getattr(paper, "abstract", "")):
        missing.append("abstract")
    if not list(getattr(paper, "authors", None) or []):
        missing.append("authors")
    if getattr(paper, "publication_year", None) is None:
        missing.append("publication_year")
    if not str(getattr(paper, "venue", "") or "").strip():
        missing.append("venue")
    if not list(getattr(paper, "keywords", None) or []):
        missing.append("keywords")
    if getattr(paper, "citation_count", None) is None:
        missing.append("citation_count")
    return missing


def crossref_values(item: dict[str, Any]) -> dict[str, Any]:
    """把 Crossref `message` 条目归一化为可回填字段，空值不写入结果。"""
    if not isinstance(item, dict):
        return {}
    values: dict[str, Any] = {}

    doi = str(item.get("DOI") or "").strip().casefold()
    if doi:
        values["doi"] = doi[:MAX_DOI_CHARS]

    abstract = _plain_text(item.get("abstract"))
    if abstract:
        values["abstract"] = abstract[:MAX_ABSTRACT_CHARS]

    authors: list[str] = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = " ".join(
            part
            for part in (
                str(author.get("given") or "").strip(),
                str(author.get("family") or "").strip(),
            )
            if part
        )
        name = name or str(author.get("name") or "").strip()
        if name:
            authors.append(name[:MAX_AUTHOR_CHARS])
    if authors:
        values["authors"] = authors

    year = _crossref_year(item)
    if year is not None:
        values["publication_year"] = year

    venue = _first_text(item.get("container-title")) or _first_text(item.get("event", {}).get("name"))
    if venue:
        values["venue"] = venue[:MAX_VENUE_CHARS]

    keywords = [
        str(subject).strip()[:MAX_KEYWORD_CHARS] for subject in item.get("subject") or [] if str(subject).strip()
    ]
    if keywords:
        values["keywords"] = list(dict.fromkeys(keywords))

    citation_count = item.get("is-referenced-by-count")
    if isinstance(citation_count, int) and not isinstance(citation_count, bool) and citation_count >= 0:
        values["citation_count"] = citation_count
    return values


def select_crossref_item(items: list[dict[str, Any]], paper: Any) -> dict[str, Any] | None:
    """在标题检索结果中选出唯一严格匹配的条目；命中不唯一时宁可不回填。"""
    target_title = normalize_paper_title(str(getattr(paper, "title", "") or ""))
    if not target_title:
        return None
    matches = [
        item
        for item in items
        if isinstance(item, dict) and normalize_paper_title(_first_text(item.get("title"))) == target_title
    ]
    if not matches:
        return None
    target_year = getattr(paper, "publication_year", None)
    if target_year is not None:
        exact_year = [item for item in matches if _crossref_year(item) == int(target_year)]
        if exact_year:
            matches = exact_year
    return matches[0] if len(matches) == 1 else None


class CrossrefClient:
    """Crossref REST API 的最小只读客户端。"""

    def __init__(self) -> None:
        self.base_url = os.getenv("CROSSREF_API_BASE", "https://api.crossref.org").rstrip("/")
        self.timeout = httpx.Timeout(20.0, connect=10.0)
        self.headers = {"User-Agent": "ResearchCompass/0.11"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, trust_env=False) as client:
            try:
                response = await client.get(f"{self.base_url}{path}", params=params)
            except httpx.HTTPError as exc:
                raise ResearchPaperMetadataError("crossref_unavailable", "Crossref 请求失败") from exc
        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            raise ResearchPaperMetadataError("crossref_unavailable", f"Crossref 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResearchPaperMetadataError("crossref_invalid_response", "Crossref 返回非 JSON 响应") from exc
        if not isinstance(payload, dict):
            raise ResearchPaperMetadataError("crossref_invalid_response", "Crossref 响应必须是对象")
        return payload

    async def get_by_doi(self, doi: str) -> dict[str, Any] | None:
        payload = await self._get(f"/works/{quote(doi, safe='')}")
        item = payload.get("message")
        return item if isinstance(item, dict) else None

    async def search_by_title(self, title: str) -> list[dict[str, Any]]:
        payload = await self._get(
            "/works",
            {
                "query.bibliographic": title,
                "rows": CROSSREF_SEARCH_ROWS,
                "select": (
                    "DOI,title,author,container-title,issued,published-print,published-online,"
                    "abstract,subject,is-referenced-by-count,event"
                ),
            },
        )
        message = payload.get("message")
        items = message.get("items") if isinstance(message, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _crossref_year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued", "created"):
        block = item.get(key)
        parts = block.get("date-parts") if isinstance(block, dict) else None
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or not parts[0]:
            continue
        year = parts[0][0]
        if isinstance(year, int) and not isinstance(year, bool) and 1500 <= year <= datetime.now(UTC).year + 1:
            return year
    return None


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return _plain_text(value)


def _plain_text(value: Any) -> str:
    return " ".join(_JATS_TAG_PATTERN.sub(" ", str(value or "")).split())


async def _lookup_crossref(paper: Any) -> tuple[dict[str, Any], str]:
    """返回 Crossref 候选值与命中方式；外部失败按"未命中"处理并记录日志。"""
    doi = str(getattr(paper, "doi", "") or "").strip()
    client = CrossrefClient()
    try:
        item = await client.get_by_doi(doi) if doi else None
        if item is not None:
            return crossref_values(item), "doi"
        item = select_crossref_item(await client.search_by_title(str(getattr(paper, "title", "") or "")), paper)
    except ResearchPaperMetadataError as exc:
        logger.warning("Crossref 元数据查询失败: %s", exc.error_type)
        return {}, "unavailable"
    return (crossref_values(item), "title") if item is not None else ({}, "none")


async def _lookup_semantic_scholar(paper: Any) -> dict[str, Any]:
    """用 Semantic Scholar 严格匹配补齐 Crossref 未覆盖的字段；失败或超时不回填。"""
    try:
        item = await asyncio.wait_for(
            SemanticScholarClient().resolve_paper(paper), timeout=SEMANTIC_SCHOLAR_TIMEOUT_SECONDS
        )
    except TimeoutError:
        logger.warning("Semantic Scholar 元数据补齐超时")
        return {}
    except SemanticScholarError as exc:
        logger.info("Semantic Scholar 元数据补齐未命中: %s", exc.error_type)
        return {}
    values: dict[str, Any] = {}
    external_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    doi = str(external_ids.get("DOI") or "").strip().casefold()
    if doi:
        values["doi"] = doi[:MAX_DOI_CHARS]
    abstract = _plain_text(item.get("abstract"))
    if abstract:
        values["abstract"] = abstract[:MAX_ABSTRACT_CHARS]
    year = item.get("year")
    if isinstance(year, int) and not isinstance(year, bool) and 1500 <= year <= datetime.now(UTC).year + 1:
        values["publication_year"] = year
    venue = str(item.get("venue") or "").strip()
    if venue:
        values["venue"] = venue[:MAX_VENUE_CHARS]
    keywords = [
        str(value).strip()[:MAX_KEYWORD_CHARS] for value in item.get("fieldsOfStudy") or [] if str(value).strip()
    ]
    if keywords:
        values["keywords"] = list(dict.fromkeys(keywords))
    citation_count = item.get("citationCount")
    if isinstance(citation_count, int) and not isinstance(citation_count, bool) and citation_count >= 0:
        values["citation_count"] = citation_count
    return values


def _pick_filled_fields(
    missing: list[str],
    crossref_candidates: dict[str, Any],
    semantic_candidates: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """按 Crossref 优先、Semantic Scholar 兜底的顺序挑选可回填值。"""
    filled: dict[str, Any] = {}
    field_sources: dict[str, str] = {}
    for field in missing:
        for source, candidates in (
            ("crossref", crossref_candidates),
            ("semantic_scholar", semantic_candidates),
        ):
            value = candidates.get(field)
            if value in (None, "", []):
                continue
            filled[field] = value
            field_sources[field] = source
            break
    return filled, field_sources


async def _persist_enrichment(
    *,
    repo: AcademicPaperRepository,
    paper: Any,
    kb_id: str,
    paper_id: str,
    filled: dict[str, Any],
    current_user: User,
) -> tuple[Any, dict[str, Any] | None]:
    """写入补全字段；有原文文件时同步重建分块索引。"""
    if not paper.file_id:
        updated = await repo.update(kb_id=kb_id, paper_id=paper_id, data=dict(filled))
        if updated is None:
            raise ResearchPaperMetadataError("paper_not_found", "论文不存在")
        return updated, None

    updated = await repo.update_metadata(
        kb_id=kb_id,
        paper_id=paper_id,
        data={**filled, "metadata_status": "pending_reindex", "metadata_error": None},
    )
    if updated is None:
        raise ResearchPaperMetadataError("paper_not_found", "论文不存在")
    revision = int(updated.metadata_revision or 1)
    try:
        task, created = await _enqueue_paper_reindex(
            kb_id=kb_id,
            paper_id=paper_id,
            file_id=str(updated.file_id),
            paper_title=str(updated.title),
            operator_id=str(current_user.uid),
            revision=revision,
        )
    except Exception as exc:
        await repo.fail_reindex(
            kb_id=kb_id,
            paper_id=paper_id,
            revision=revision,
            error="论文元数据同步任务提交失败",
        )
        raise ResearchPaperMetadataError("metadata_reindex_enqueue_failed", "论文元数据同步任务提交失败") from exc
    return updated, {"task_id": task.id, "status": task.status, "created": created}


async def enrich_paper_metadata(*, kb_id: str, paper_id: str, current_user: User) -> dict[str, Any]:
    """补全单篇论文的缺失元数据，返回补齐字段、来源与索引同步任务。"""
    await _ensure_access(current_user, kb_id, write=True)
    repo = AcademicPaperRepository()
    paper = await repo.get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise ResearchPaperMetadataError("paper_not_found", "论文不存在")

    missing = missing_paper_fields(paper)
    if not missing:
        return {
            "paper_id": paper_id,
            "filled": {},
            "field_sources": {},
            "sources": [],
            "missing_fields": [],
            "paper": _serialize_paper(paper),
            "reindex_task": None,
            "message": "论文元数据已完整，无需补全",
        }

    crossref_candidates, crossref_match = await _lookup_crossref(paper)
    semantic_candidates: dict[str, Any] = {}
    if any(not crossref_candidates.get(field) for field in missing):
        semantic_candidates = await _lookup_semantic_scholar(paper)

    filled, field_sources = _pick_filled_fields(missing, crossref_candidates, semantic_candidates)
    if not filled:
        raise ResearchPaperMetadataError(
            "metadata_not_found",
            "Crossref 与 Semantic Scholar 都没有可补齐的字段",
        )

    updated, reindex_task = await _persist_enrichment(
        repo=repo,
        paper=paper,
        kb_id=kb_id,
        paper_id=paper_id,
        filled=filled,
        current_user=current_user,
    )
    filled_labels = "、".join(FIELD_LABELS[field] for field in filled)
    return {
        "paper_id": paper_id,
        "filled": filled,
        "field_sources": field_sources,
        "sources": [
            {"source": "crossref", "match": crossref_match},
            {"source": "semantic_scholar", "match": "resolved" if semantic_candidates else "none"},
        ],
        "missing_fields": [field for field in missing if field not in filled],
        "paper": _serialize_paper(updated),
        "reindex_task": reindex_task,
        "message": (
            f"已补全 {filled_labels}；人工校正过的字段不会被覆盖" + ("，检索索引正在后台重建" if reindex_task else "")
        ),
    }


__all__ = [
    "ResearchPaperMetadataError",
    "crossref_values",
    "enrich_paper_metadata",
    "missing_paper_fields",
    "select_crossref_item",
]
