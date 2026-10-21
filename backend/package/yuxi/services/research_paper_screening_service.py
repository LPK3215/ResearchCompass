"""ResearchCompass 论文筛选服务。

系统综述的标题 / 摘要与全文筛选阶段：用户逐篇或批量标记纳入、排除与理由，
服务只记录人工判断并输出 PRISMA 式计数，不替用户做纳入决策。筛选结果是个人
工作记录，按用户隔离，与论文标签采用同一权限口径（可读该知识库即可读写自己
的筛选记录）。
"""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User

SCREENING_DECISIONS = ("included", "excluded", "pending")
MAX_SCREENING_BATCH = 200
MAX_SCREENING_REASON = 500
DECISION_LABELS = {"included": "纳入", "excluded": "排除", "pending": "待定"}


class ResearchScreeningError(RuntimeError):
    """论文筛选的可预期失败。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _paper_row(paper: Any, screening: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "paper_id": str(paper.paper_id),
        "title": str(paper.title or ""),
        "publication_year": paper.publication_year,
        "venue": str(paper.venue or ""),
        "doi": str(paper.doi or ""),
        "decision": str((screening or {}).get("decision") or "pending"),
        "reason": str((screening or {}).get("reason") or ""),
    }


async def _load_screened_papers(
    kb_id: str, uid: str, *, paper_ids: list[str] | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """读取论文并附上当前用户的筛选决定；传入 paper_ids 时只加载这些论文。"""
    repo = AcademicPaperRepository()
    screenings = await repo.list_screenings(kb_id=kb_id, uid=uid)
    counts = await repo.count_screening_summary(kb_id=kb_id, uid=uid)
    if paper_ids is not None:
        papers = await repo.list_by_paper_ids(kb_id=kb_id, paper_ids=paper_ids)
        papers = [paper for paper in papers if getattr(paper, "archived_at", None) is None]
    else:
        papers = []
        async for batch in repo.iter_export_batches(kb_id=kb_id):
            papers.extend(batch)
    return [_paper_row(paper, screenings.get(str(paper.paper_id))) for paper in papers], counts


async def get_paper_screening(
    *,
    kb_id: str,
    current_user: User,
    decision: str | None = None,
    paper_ids: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """返回筛选计数与逐篇筛选结果；可按结论或论文 ID 范围过滤。"""
    if decision is not None and decision not in SCREENING_DECISIONS:
        raise ResearchScreeningError("screening_decision_invalid", "筛选结论必须是 included、excluded 或 pending")
    await _ensure_access(current_user, kb_id)
    rows, counts = await _load_screened_papers(kb_id, str(current_user.uid), paper_ids=paper_ids)
    paper_id_set = set(paper_ids or [])
    selected = [
        row
        for row in rows
        if (decision is None or row["decision"] == decision) and (not paper_id_set or row["paper_id"] in paper_id_set)
    ]
    normalized_limit = min(max(int(limit), 1), 200)
    return {
        "kb_id": kb_id,
        "counts": counts,
        "items": selected[:normalized_limit],
        "total": len(selected),
        "limit": normalized_limit,
        "note": "筛选结果记录用户人工判断；AI 只能读取，不得代替用户决定纳入或排除。",
    }


async def set_paper_screening(
    *,
    kb_id: str,
    paper_ids: list[str],
    decision: str,
    reason: str,
    current_user: User,
) -> dict[str, Any]:
    """批量写入纳入 / 排除决定；decision=pending 表示清除决定回到待定。"""
    if decision not in SCREENING_DECISIONS:
        raise ResearchScreeningError("screening_decision_invalid", "筛选结论必须是 included、excluded 或 pending")
    normalized_ids = list(dict.fromkeys(str(paper_id).strip() for paper_id in paper_ids if str(paper_id).strip()))
    if not normalized_ids:
        raise ResearchScreeningError("screening_paper_ids_empty", "至少选择一篇论文")
    if len(normalized_ids) > MAX_SCREENING_BATCH:
        raise ResearchScreeningError("screening_batch_too_large", f"单次最多筛选 {MAX_SCREENING_BATCH} 篇论文")
    normalized_reason = str(reason or "").strip()
    if len(normalized_reason) > MAX_SCREENING_REASON:
        raise ResearchScreeningError("screening_reason_too_long", f"筛选理由不能超过 {MAX_SCREENING_REASON} 个字符")
    await _ensure_access(current_user, kb_id)
    papers = await AcademicPaperRepository().list_by_paper_ids(kb_id=kb_id, paper_ids=normalized_ids)
    missing = set(normalized_ids) - {str(paper.paper_id) for paper in papers}
    if missing:
        raise ResearchScreeningError("paper_not_found", "部分论文不存在于当前知识库")
    archived = {
        str(paper.paper_id)
        for paper in papers
        if getattr(paper, "archived_at", None) is not None
    }
    if archived:
        raise ResearchScreeningError("paper_not_found", "部分论文已归档，不能参与筛选")

    repo = AcademicPaperRepository()
    uid = str(current_user.uid)
    if decision == "pending":
        cleared = await repo.clear_screenings(kb_id=kb_id, paper_ids=normalized_ids, uid=uid)
        return {"decision": decision, "updated": cleared, "paper_ids": normalized_ids}
    for paper_id in normalized_ids:
        await repo.set_screening(kb_id=kb_id, paper_id=paper_id, uid=uid, decision=decision, reason=normalized_reason)
    return {"decision": decision, "updated": len(normalized_ids), "paper_ids": normalized_ids}


def _csv_cell(value: Any) -> str:
    """前缀化公式样式单元格，避免表格软件执行导出内容。"""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


async def export_screening_csv(*, kb_id: str, current_user: User) -> tuple[str, bytes, str]:
    """把当前用户的逐篇筛选结果导出为 CSV，UTF-8 BOM 兼容常见表格软件。"""
    await _ensure_access(current_user, kb_id)
    rows, counts = await _load_screened_papers(kb_id, str(current_user.uid))
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["paper_id", "title", "publication_year", "venue", "doi", "decision", "reason"])
    summary = (
        f"识别 {counts['identified']} / 纳入 {counts['included']} / "
        f"排除 {counts['excluded']} / 待定 {counts['pending']}"
    )
    writer.writerow(["summary", summary, "", "", "", "", ""])
    for row in rows:
        writer.writerow(
            [
                _csv_cell(row["paper_id"]),
                _csv_cell(row["title"]),
                _csv_cell(row["publication_year"]),
                _csv_cell(row["venue"]),
                _csv_cell(row["doi"]),
                DECISION_LABELS[row["decision"]],
                _csv_cell(row["reason"]),
            ]
        )
    return f"paper_screening_{kb_id}.csv", buffer.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"


__all__ = [
    "DECISION_LABELS",
    "MAX_SCREENING_BATCH",
    "ResearchScreeningError",
    "SCREENING_DECISIONS",
    "export_screening_csv",
    "get_paper_screening",
    "set_paper_screening",
]
