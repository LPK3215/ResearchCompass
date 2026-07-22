from __future__ import annotations

from collections import defaultdict
from typing import Any

from yuxi.repositories.academic_graph_repository import AcademicGraphRepository
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User


class AcademicTrendError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _trend_keywords(rows: list[tuple[int | None, list[str], int | None]]) -> list[dict[str, Any]]:
    keyword_year_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    keyword_display: dict[str, str] = {}
    for year, keywords, _ in rows:
        if year is None:
            continue
        for keyword in set(keywords):
            normalized = " ".join(keyword.casefold().split())
            if not normalized:
                continue
            keyword_display.setdefault(normalized, keyword)
            keyword_year_counts[normalized][int(year)] += 1

    result = []
    for normalized, counts in keyword_year_counts.items():
        years = sorted(counts)
        result.append(
            {
                "keyword": keyword_display[normalized],
                "total": sum(counts.values()),
                "first_year": years[0],
                "last_year": years[-1],
                "years": [{"year": year, "count": counts[year]} for year in years],
            }
        )
    return sorted(result, key=lambda item: (-item["total"], item["keyword"].casefold()))


def _emerging_keywords(keyword_trends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    years = [year for item in keyword_trends for year in (item.get("last_year"),) if year is not None]
    if not years:
        return []
    latest_year = max(years)
    emerging = []
    for item in keyword_trends:
        recent_count = sum(
            int(year_item["count"])
            for year_item in item["years"]
            if int(year_item["year"]) >= latest_year - 1
        )
        previous_count = sum(
            int(year_item["count"])
            for year_item in item["years"]
            if latest_year - 3 <= int(year_item["year"]) <= latest_year - 2
        )
        if int(item["last_year"]) < latest_year - 1 or recent_count <= 0:
            continue
        is_new = int(item["first_year"]) >= latest_year - 1
        growth = recent_count - previous_count
        if not is_new and growth <= 0:
            continue
        emerging.append(
            {
                "keyword": item["keyword"],
                "first_year": item["first_year"],
                "last_year": item["last_year"],
                "recent_count": recent_count,
                "previous_count": previous_count,
                "growth": growth,
                "is_new": is_new,
            }
        )
    return sorted(
        emerging,
        key=lambda item: (-item["growth"], -item["recent_count"], item["keyword"].casefold()),
    )


async def get_academic_trends(
    *,
    kb_id: str,
    current_user: User,
    year_from: int | None,
    year_to: int | None,
    offset: int,
    limit: int,
    top_keywords: int,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    if year_from is not None and year_to is not None and year_from > year_to:
        raise AcademicTrendError("invalid_filter", "year_from 不能大于 year_to")
    rows, total = await AcademicPaperRepository().list_trend_records(
        kb_id=kb_id,
        year_from=year_from,
        year_to=year_to,
        offset=offset,
        limit=limit,
    )
    publication_counts: dict[int, int] = defaultdict(int)
    citation_totals: dict[int, int] = defaultdict(int)
    for year, _, citation_count in rows:
        if year is None:
            continue
        publication_counts[int(year)] += 1
        citation_totals[int(year)] += max(int(citation_count or 0), 0)
    publication_trend = [
        {
            "year": year,
            "papers": publication_counts[year],
            "citations": citation_totals[year],
        }
        for year in sorted(publication_counts)
    ]
    keywords = _trend_keywords(rows)
    emerging_directions = _emerging_keywords(keywords)
    keyword_totals = [
        {"keyword": item["keyword"], "total": item["total"]}
        for item in keywords[: max(int(top_keywords), 1)]
    ]
    citation_trend = await AcademicGraphRepository().citation_trend(
        kb_id=kb_id,
        year_from=year_from,
        year_to=year_to,
    )
    return {
        "scope": {
            "year_from": year_from,
            "year_to": year_to,
            "offset": offset,
            "limit": limit,
            "total_papers": total,
            "returned_papers": len(rows),
            "has_more": offset + len(rows) < total,
        },
        "publication_trend": publication_trend,
        "keyword_totals": keyword_totals,
        "keyword_trends": keywords[: max(int(top_keywords), 1)],
        "emerging_directions": emerging_directions[:10],
        "citation_trend": citation_trend,
    }


__all__ = ["AcademicTrendError", "get_academic_trends"]
