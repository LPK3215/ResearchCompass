"""ResearchCompass 研究机会雷达服务。

本模块是本仓库作者在开源智能体框架 Yuxi 之上设计的研究机会识别业务：基于知识库内
论文的关键词增长、近期性、覆盖度、证据量与引用图谱连接缺口，按确定性证据信号计算
研究机会分数与置信度，并给出可追溯的证据论文与下一步行动建议。论文与图谱数据由
Yuxi 仓库提供；本模块定义机会评分公式、置信度规则与机会雷达的方法学说明。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from yuxi.repositories.academic_graph_repository import AcademicGraphRepository
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User


class AcademicOpportunityError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _normalize_keyword(value: Any) -> tuple[str, str] | None:
    display = str(value or "").strip()
    normalized = " ".join(display.casefold().split())
    return (normalized, display) if normalized else None


def _opportunity_score(
    *, paper_count: int, recent_count: int, growth: int, is_new: bool, graph_gap: float
) -> float:
    momentum = min(40.0, 15.0 + recent_count * 5.0 + max(growth, 0) * 5.0)
    recency = 15.0 if is_new else min(15.0, 8.0 + max(growth, 0) * 2.0)
    scarcity = max(4.0, 20.0 - max(paper_count - 2, 0) * 2.0)
    evidence = min(15.0, paper_count * 3.0)
    return round(min(100.0, momentum + recency + scarcity + evidence + graph_gap), 1)


def _confidence(*, paper_count: int, graph_coverage: float) -> str:
    if paper_count >= 5 and graph_coverage >= 0.6:
        return "high"
    if paper_count >= 3 or graph_coverage > 0:
        return "medium"
    return "low"


async def get_academic_opportunities(
    *,
    kb_id: str,
    current_user: User,
    year_from: int | None,
    year_to: int | None,
    limit: int,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    if year_from is not None and year_to is not None and year_from > year_to:
        raise AcademicOpportunityError("invalid_filter", "year_from 不能大于 year_to")
    normalized_limit = min(max(int(limit), 1), 20)
    papers = await AcademicPaperRepository().list_for_opportunity_radar(
        kb_id=kb_id,
        year_from=year_from,
        year_to=year_to,
    )
    years = [int(paper.publication_year) for paper in papers if paper.publication_year is not None]
    if not years:
        return {
            "scope": {
                "year_from": year_from,
                "year_to": year_to,
                "total_papers": len(papers),
                "latest_year": None,
            },
            "opportunities": [],
            "methodology": _methodology(graph_available=False),
        }

    latest_year = max(years)
    recent_start = latest_year - 1
    previous_start = latest_year - 3
    keyword_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "keyword": "",
            "paper_ids": set(),
            "years": defaultdict(int),
            "citations": 0,
        }
    )
    papers_by_id = {}
    for paper in papers:
        paper_id = str(paper.paper_id)
        papers_by_id[paper_id] = paper
        year = int(paper.publication_year) if paper.publication_year is not None else None
        normalized_keywords: dict[str, str] = {}
        for raw_keyword in paper.keywords if isinstance(paper.keywords, list) else []:
            normalized = _normalize_keyword(raw_keyword)
            if normalized is None:
                continue
            key, display = normalized
            normalized_keywords.setdefault(key, display)
        for key, display in normalized_keywords.items():
            stat = keyword_stats[key]
            stat["keyword"] = stat["keyword"] or display
            stat["paper_ids"].add(paper_id)
            if year is not None:
                stat["years"][year] += 1
            stat["citations"] += max(int(paper.citation_count or 0), 0)

    degrees = await AcademicGraphRepository().list_library_paper_citation_degrees(kb_id=kb_id)
    opportunities = []
    for stat in keyword_stats.values():
        paper_ids = list(stat["paper_ids"])
        keyword_years = sorted(stat["years"])
        if not keyword_years:
            continue
        recent_count = sum(stat["years"][year] for year in keyword_years if year >= recent_start)
        previous_count = sum(
            stat["years"][year] for year in keyword_years if previous_start <= year <= latest_year - 2
        )
        growth = recent_count - previous_count
        first_year = keyword_years[0]
        last_year = keyword_years[-1]
        is_new = first_year >= recent_start
        if recent_count <= 0 or (not is_new and growth <= 0):
            continue

        linked_degrees = [
            degrees[int(papers_by_id[paper_id].id)]
            for paper_id in paper_ids
            if int(papers_by_id[paper_id].id) in degrees
        ]
        graph_coverage = len(linked_degrees) / len(paper_ids)
        average_degree = (
            sum(item["incoming"] + item["outgoing"] for item in linked_degrees) / len(linked_degrees)
            if linked_degrees
            else 0.0
        )
        graph_gap = min(10.0, max(0.0, 10.0 - average_degree)) if linked_degrees else 0.0
        paper_count = len(paper_ids)
        score = _opportunity_score(
            paper_count=paper_count,
            recent_count=recent_count,
            growth=growth,
            is_new=is_new,
            graph_gap=graph_gap,
        )
        reasons = [
            (
                f"近两年出现 {recent_count} 篇相关论文，较此前窗口增加 {growth} 篇"
                if growth > 0
                else "近两年首次出现相关论文"
            ),
            f"当前库内仅有 {paper_count} 篇相关论文，主题覆盖仍较集中",
        ]
        if linked_degrees:
            reasons.append(f"相关论文平均引用连接度 {average_degree:.1f}，可继续核查潜在连接缺口")
        else:
            reasons.append("相关论文尚未接入本地引用图谱，本项未计入图谱缺口分")
        evidence_papers = []
        for paper_id in sorted(
            paper_ids,
            key=lambda value: (
                -int(papers_by_id[value].publication_year or 0),
                -int(papers_by_id[value].citation_count or 0),
                value,
            ),
        )[:3]:
            paper = papers_by_id[paper_id]
            evidence_papers.append(
                {
                    "paper_id": paper.paper_id,
                    "title": paper.title,
                    "publication_year": paper.publication_year,
                    "citation_count": int(paper.citation_count or 0),
                    "abstract": (paper.abstract or "")[:360] or None,
                    "citation_degree": degrees.get(int(paper.id), {"incoming": 0, "outgoing": 0}),
                }
            )
        opportunities.append(
            {
                "keyword": stat["keyword"],
                "score": score,
                "confidence": _confidence(paper_count=paper_count, graph_coverage=graph_coverage),
                "signals": {
                    "first_year": first_year,
                    "last_year": last_year,
                    "recent_count": recent_count,
                    "previous_count": previous_count,
                    "growth": growth,
                    "is_new": is_new,
                    "paper_count": paper_count,
                    "average_citations": round(stat["citations"] / paper_count, 2),
                },
                "coverage": {
                    "graph_linked_papers": len(linked_degrees),
                    "graph_coverage_ratio": round(graph_coverage, 2),
                    "average_citation_degree": round(average_degree, 2),
                },
                "reasons": reasons,
                "evidence_papers": evidence_papers,
                "next_actions": [
                    f"围绕“{stat['keyword']}”检索方法差异与未解决限制",
                    "对照证据论文的实验数据、评估指标和引用邻域进行可复现性核查",
                ],
            }
        )

    opportunities.sort(key=lambda item: (-item["score"], -item["signals"]["growth"], item["keyword"].casefold()))
    graph_available = bool(degrees)
    return {
        "scope": {
            "year_from": year_from,
            "year_to": year_to,
            "total_papers": len(papers),
            "latest_year": latest_year,
        },
        "opportunities": opportunities[:normalized_limit],
        "methodology": _methodology(graph_available=graph_available),
    }


def _methodology(*, graph_available: bool) -> dict[str, Any]:
    return {
        "type": "deterministic_evidence_signal_v1",
        "graph_available": graph_available,
        "description": "机会分数由关键词增长、近期性、论文覆盖度、证据量和可用的引用连接缺口共同计算。",
        "limitation": "机会雷达是证据排序，不等同于经过领域专家确认的研究空白。",
    }


__all__ = ["AcademicOpportunityError", "get_academic_opportunities"]
