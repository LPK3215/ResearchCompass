"""ResearchCompass 引文滚雪球服务。

从库内单篇论文出发，拉取它的参考文献或被引文献，支持勾选后批量纳入本地库：
默认只写元数据（不参与全文检索），开放获取条目可选择同时下载 PDF 走既有导入
链路。所有写入按 DOI 优先、标题兜底幂等，重复导入不会产生新记录。
"""

from __future__ import annotations

import re
from typing import Any

from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.services.semantic_scholar_service import SemanticScholarClient, SemanticScholarError
from yuxi.storage.postgres.models_business import User
from yuxi.utils import hashstr

RELATED_DIRECTIONS = ("references", "citations")
MAX_SNOWBALL_LIMIT = 50


class ResearchCitationSnowballError(RuntimeError):
    """引文滚雪球的可预期失败。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _identity(doi: str | None, title: str) -> str:
    normalized_doi = str(doi or "").strip().casefold()
    if normalized_doi:
        return f"doi:{normalized_doi}"
    normalized_title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(title or "").lower())[:80]
    return f"title:{normalized_title}" if normalized_title else ""


def _paper_identity(paper: Any) -> str:
    return _identity(getattr(paper, "doi", None), str(getattr(paper, "title", "") or ""))


async def _existing_identities(kb_id: str) -> set[str]:
    """已入库标识包含归档论文：合并隐藏的重复条目不能被滚雪球重新导入。"""
    identities: set[str] = set()
    async for batch in AcademicPaperRepository().iter_export_batches(kb_id=kb_id, include_archived=True):
        for paper in batch:
            identity = _paper_identity(paper)
            if identity:
                identities.add(identity)
    return identities


def _related_item(edge: dict[str, Any], field: str, existing: set[str]) -> dict[str, Any] | None:
    related = edge.get(field)
    if not isinstance(related, dict) or not related.get("paperId") or not related.get("title"):
        return None
    external_ids = related.get("externalIds") if isinstance(related.get("externalIds"), dict) else {}
    doi = str(external_ids.get("DOI") or "").strip() or None
    open_access = related.get("openAccessPdf") if isinstance(related.get("openAccessPdf"), dict) else {}
    authors = [
        str(author.get("name")).strip()
        for author in related.get("authors") or []
        if isinstance(author, dict) and str(author.get("name") or "").strip()
    ]
    return {
        "s2_paper_id": str(related["paperId"]),
        "title": str(related["title"]).strip(),
        "abstract": str(related.get("abstract") or "").strip() or None,
        "authors": authors,
        "publication_year": related.get("year"),
        "venue": str(related.get("venue") or "").strip() or None,
        "doi": doi,
        "citation_count": related.get("citationCount"),
        "is_open_access": bool(related.get("isOpenAccess")),
        "open_access_url": str(open_access.get("url") or "").strip() or None,
        "already_in_library": _identity(doi, str(related["title"])) in existing,
    }


async def list_related_papers(
    *,
    kb_id: str,
    paper_id: str,
    direction: str,
    current_user: User,
    limit: int = 20,
) -> dict[str, Any]:
    """列出某篇库内论文的参考文献或被引文献，并标注是否已在库内。"""
    if direction not in RELATED_DIRECTIONS:
        raise ResearchCitationSnowballError("snowball_invalid_direction", "方向必须是 references 或 citations")
    await _ensure_access(current_user, kb_id)
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise ResearchCitationSnowballError("paper_not_found", "论文不存在")
    normalized_limit = min(max(int(limit), 1), MAX_SNOWBALL_LIMIT)
    client = SemanticScholarClient()
    try:
        remote = await client.resolve_paper(paper)
        if direction == "references":
            edges = await client.list_references(remote["paperId"], normalized_limit)
            field = "citedPaper"
        else:
            edges = await client.list_citations(remote["paperId"], normalized_limit)
            field = "citingPaper"
    except SemanticScholarError as exc:
        retryable = exc.error_type.startswith("semantic_scholar_")
        raise ResearchCitationSnowballError(
            exc.error_type if not retryable else "semantic_scholar_unavailable", exc.message
        ) from exc
    existing = await _existing_identities(kb_id)
    items = [item for item in (_related_item(edge, field, existing) for edge in edges) if item is not None]
    return {
        "paper_id": paper_id,
        "direction": direction,
        "items": items,
        "total": len(items),
        "existing_count": sum(1 for item in items if item["already_in_library"]),
    }


async def import_related_papers(
    *,
    kb_id: str,
    paper_id: str,
    direction: str,
    paper_ids: list[str],
    include_full_text: bool,
    current_user: User,
) -> dict[str, Any]:
    """把勾选的关联文献纳入本地库：默认写元数据，开放获取条目可选下载全文。"""
    selected_ids = list(dict.fromkeys(str(item).strip() for item in paper_ids if str(item).strip()))
    if not selected_ids:
        raise ResearchCitationSnowballError("snowball_selection_empty", "至少选择一篇文献")
    if len(selected_ids) > MAX_SNOWBALL_LIMIT:
        raise ResearchCitationSnowballError("snowball_selection_too_large", f"单次最多导入 {MAX_SNOWBALL_LIMIT} 篇文献")
    listing = await list_related_papers(
        kb_id=kb_id, paper_id=paper_id, direction=direction, current_user=current_user, limit=MAX_SNOWBALL_LIMIT
    )
    selected = [item for item in listing["items"] if item["s2_paper_id"] in set(selected_ids)]
    if not selected:
        raise ResearchCitationSnowballError("snowball_selection_invalid", "所选文献不在该论文的关联列表中")

    rows: list[dict[str, Any]] = []
    queued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in selected:
        if item["already_in_library"]:
            skipped.append({"title": item["title"], "reason": "already_exists"})
            continue
        if include_full_text and item["open_access_url"]:
            try:
                from yuxi.services.academic_paper_import_service import import_external_paper

                task = await import_external_paper(
                    identifier=item["s2_paper_id"], kb_id=kb_id, current_user=current_user
                )
                queued.append({"title": item["title"], "task_id": task.get("task_id")})
            except Exception as exc:  # noqa: BLE001 - 单条入队失败不阻断其余条目
                failed.append({"title": item["title"], "reason": getattr(exc, "message", str(exc))})
            continue
        rows.append(
            {
                "paper_id": f"snow_{hashstr(f'{kb_id}:{_identity(item["doi"], item["title"])}', 24)}",
                "file_id": None,
                "title": item["title"],
                "abstract": item["abstract"],
                "authors": item["authors"],
                "publication_year": item["publication_year"],
                "venue": item["venue"],
                "doi": item["doi"],
                "keywords": [],
                "external_ids": {
                    "SemanticScholar": item["s2_paper_id"],
                    **({"DOI": item["doi"]} if item["doi"] else {}),
                },
                "citation_count": item["citation_count"],
                "metadata_source": "citation_snowball",
                "metadata_status": "extracted",
                "metadata_revision": 1,
                "indexed_revision": 0,
            }
        )

    created, conflicted = await AcademicPaperRepository().create_metadata_only_batch(kb_id=kb_id, rows=rows)
    return {
        "direction": direction,
        "metadata_imported": len(created),
        "full_text_queued": len(queued),
        "skipped": len(skipped) + conflicted,
        "failed": len(failed),
        "queued": queued,
        "skip_details": skipped[:20],
        "failure_details": failed[:20],
        "message": "元数据记录不参与全文检索；需要全文请选择包含开放获取下载的导入方式",
    }


__all__ = [
    "MAX_SNOWBALL_LIMIT",
    "RELATED_DIRECTIONS",
    "ResearchCitationSnowballError",
    "import_related_papers",
    "list_related_papers",
]
