"""ResearchCompass 外部学术检索多源适配层。

Semantic Scholar 是主数据源，OpenAlex 与 Crossref 作为补充源：三路并发检索后
按 DOI（优先）或归一化标题归并，保留各来源标记，并把单个来源的失败降级为
“该来源不可用”，不拖垮整体结果。只有全部来源失败时才返回可重试错误。
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from typing import Any

import httpx

from yuxi.services.semantic_scholar_service import (
    SemanticScholarClient,
    SemanticScholarError,
    normalize_paper_title,
)

OPENALEX_BASE = os.getenv("OPENALEX_API_BASE", "https://api.openalex.org").rstrip("/")
CROSSREF_BASE = os.getenv("CROSSREF_API_BASE", "https://api.crossref.org").rstrip("/")
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
SOURCE_LABELS = {"semantic_scholar": "Semantic Scholar", "openalex": "OpenAlex", "crossref": "Crossref"}


class ExternalSearchSourceError(RuntimeError):
    """单个外部检索来源的可预期失败。"""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(message)
        self.source = source
        self.message = message


def _quote_doi(value: Any) -> str:
    doi = str(value or "").strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    return doi


def _merge_key(item: dict[str, Any]) -> str:
    doi = _quote_doi(item.get("doi"))
    if doi:
        return f"doi:{doi}"
    return f"title:{normalize_paper_title(str(item.get('title') or ''))}"


def merge_source_results(items_by_source: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """按来源顺序归并检索结果，DOI 或标题命中时合并字段并记录来源。"""
    merged: dict[str, dict[str, Any]] = {}
    for source, items in items_by_source:
        for item in items:
            key = _merge_key(item)
            if not key or key == "title:":
                continue
            existing = merged.get(key)
            if existing is None:
                merged[key] = {**item, "sources": [source]}
                continue
            existing["sources"].append(source)
            for field in ("doi", "abstract", "venue", "open_access_url"):
                if not existing.get(field) and item.get(field):
                    existing[field] = item[field]
            if not existing.get("paper_id") and item.get("paper_id"):
                existing["paper_id"] = item["paper_id"]
            if item.get("authors") and not existing.get("authors"):
                existing["authors"] = item["authors"]
            counts = [
                value
                for value in (existing.get("citation_count"), item.get("citation_count"))
                if isinstance(value, int)
            ]
            if counts:
                existing["citation_count"] = max(counts)
            existing["is_open_access"] = bool(existing.get("is_open_access") or item.get("is_open_access"))
            existing["external_ids"] = {**(item.get("external_ids") or {}), **(existing.get("external_ids") or {})}
    return list(merged.values())


async def _get_json(url: str, params: dict[str, Any], *, source: str) -> dict[str, Any]:
    headers = {"User-Agent": "ResearchCompass/0.11 (academic-search)"}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers, trust_env=False) as client:
            response = await client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise ExternalSearchSourceError(source, f"{SOURCE_LABELS[source]} 请求超时") from exc
    except httpx.HTTPError as exc:
        raise ExternalSearchSourceError(source, f"{SOURCE_LABELS[source]} 网络请求失败") from exc
    if response.status_code == 429:
        raise ExternalSearchSourceError(source, f"{SOURCE_LABELS[source]} 触发限流，请稍后重试")
    if response.status_code >= 400:
        raise ExternalSearchSourceError(source, f"{SOURCE_LABELS[source]} 返回 HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ExternalSearchSourceError(source, f"{SOURCE_LABELS[source]} 返回非对象响应")
    return payload


def _abstract_from_inverted_index(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in value.items():
        for index in indexes if isinstance(indexes, list) else []:
            if isinstance(index, int):
                positions.append((index, str(word)))
    positions.sort()
    return " ".join(word for _, word in positions)


def _strip_markup(value: Any) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return " ".join(text.split())


async def search_openalex(query: str, limit: int) -> list[dict[str, Any]]:
    payload = await _get_json(
        f"{OPENALEX_BASE}/works",
        {"search": query, "per-page": min(max(limit, 1), 20)},
        source="openalex",
    )
    results = payload.get("results")
    if not isinstance(results, list):
        raise ExternalSearchSourceError("openalex", "OpenAlex 响应缺少 results 数组")
    items = []
    for work in results:
        if not isinstance(work, dict) or not work.get("display_name"):
            continue
        doi = _quote_doi(work.get("doi"))
        location = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
        source_info = location.get("source") if isinstance(location.get("source"), dict) else {}
        open_access = work.get("open_access") if isinstance(work.get("open_access"), dict) else {}
        items.append(
            {
                "paper_id": f"DOI:{doi}" if doi else str(work.get("id") or ""),
                "title": str(work.get("display_name") or "").strip(),
                "abstract": _abstract_from_inverted_index(work.get("abstract_inverted_index")),
                "authors": [
                    str(author.get("author", {}).get("display_name") or "").strip()
                    for author in work.get("authorships") or []
                    if isinstance(author, dict) and isinstance(author.get("author"), dict)
                ],
                "publication_year": work.get("publication_year"),
                "venue": str(source_info.get("display_name") or "").strip() or None,
                "doi": doi or None,
                "citation_count": work.get("cited_by_count"),
                "is_open_access": bool(open_access.get("is_oa")),
                "open_access_url": str(open_access.get("oa_url") or "").strip() or None,
                "external_ids": {"OpenAlex": str(work.get("id") or "").rsplit("/", 1)[-1]},
            }
        )
    return items


async def search_crossref(query: str, limit: int) -> list[dict[str, Any]]:
    payload = await _get_json(
        f"{CROSSREF_BASE}/works",
        {"query": query, "rows": min(max(limit, 1), 20)},
        source="crossref",
    )
    message = payload.get("message")
    results = message.get("items") if isinstance(message, dict) else None
    if not isinstance(results, list):
        raise ExternalSearchSourceError("crossref", "Crossref 响应缺少 items 数组")
    items = []
    for work in results:
        if not isinstance(work, dict) or not work.get("title"):
            continue
        doi = _quote_doi(work.get("DOI"))
        issued = work.get("issued") if isinstance(work.get("issued"), dict) else {}
        date_parts = issued.get("date-parts")
        year = date_parts[0][0] if isinstance(date_parts, list) and date_parts and date_parts[0] else None
        pdf_url = next(
            (
                str(link.get("URL") or "").strip()
                for link in work.get("link") or []
                if isinstance(link, dict) and "pdf" in str(link.get("content-type") or "").lower()
            ),
            None,
        )
        items.append(
            {
                "paper_id": f"DOI:{doi}" if doi else "",
                "title": _strip_markup((work.get("title") or [""])[0]),
                "abstract": _strip_markup(work.get("abstract")),
                "authors": [
                    " ".join(part for part in (author.get("given"), author.get("family")) if part).strip()
                    for author in work.get("author") or []
                    if isinstance(author, dict)
                ],
                "publication_year": year,
                "venue": _strip_markup((work.get("container-title") or [""])[0]) or None,
                "doi": doi or None,
                "citation_count": work.get("is-referenced-by-count"),
                "is_open_access": bool(pdf_url),
                "open_access_url": pdf_url or None,
                "external_ids": {"DOI": doi} if doi else {},
            }
        )
    return items


def _semantic_scholar_item(paper: dict[str, Any]) -> dict[str, Any]:
    external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    open_access = paper.get("openAccessPdf") if isinstance(paper.get("openAccessPdf"), dict) else {}
    open_access_url = str(open_access.get("url") or "").strip() or None
    if not open_access_url:
        arxiv_id = str(external_ids.get("ArXiv") or "").strip()
        if arxiv_id:
            open_access_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    return {
        "paper_id": paper.get("paperId"),
        "title": paper.get("title"),
        "abstract": paper.get("abstract"),
        "authors": [
            author.get("name")
            for author in paper.get("authors") or []
            if isinstance(author, dict) and author.get("name")
        ],
        "publication_year": paper.get("year"),
        "venue": paper.get("venue"),
        "doi": external_ids.get("DOI"),
        "citation_count": paper.get("citationCount"),
        "is_open_access": bool(paper.get("isOpenAccess")) or bool(open_access_url),
        "open_access_url": open_access_url,
        "external_ids": external_ids,
    }


async def search_multi_source(query: str, limit: int) -> dict[str, Any]:
    """并发检索三个来源；返回归并条目与逐源状态，全部失败时抛可重试错误。"""
    client = SemanticScholarClient()

    async def search_semantic_scholar() -> list[dict[str, Any]]:
        papers = await client.search(query, limit)
        return [_semantic_scholar_item(paper) for paper in papers]

    sources = (
        ("semantic_scholar", search_semantic_scholar),
        ("openalex", lambda: search_openalex(query, limit)),
        ("crossref", lambda: search_crossref(query, limit)),
    )
    outcomes = await asyncio.gather(*(operation() for _, operation in sources), return_exceptions=True)

    items_by_source: list[tuple[str, list[dict[str, Any]]]] = []
    statuses: list[dict[str, Any]] = []
    for (source, _), outcome in zip(sources, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            retryable = isinstance(outcome, (SemanticScholarError, ExternalSearchSourceError)) or isinstance(
                outcome, (TimeoutError, ConnectionError)
            )
            statuses.append(
                {
                    "source": source,
                    "label": SOURCE_LABELS[source],
                    "status": "failed",
                    "retryable": retryable,
                    "message": str(getattr(outcome, "message", outcome)),
                }
            )
            continue
        statuses.append({"source": source, "label": SOURCE_LABELS[source], "status": "ok", "count": len(outcome)})
        items_by_source.append((source, outcome))

    if not items_by_source:
        raise ExternalSearchSourceError("all_sources", "所有外部检索来源均不可用，请稍后重试")
    items = merge_source_results(items_by_source)
    return {
        "items": items,
        "sources": statuses,
        "partial": any(status["status"] == "failed" for status in statuses),
    }


__all__ = [
    "ExternalSearchSourceError",
    "SOURCE_LABELS",
    "merge_source_results",
    "search_crossref",
    "search_multi_source",
    "search_openalex",
]
