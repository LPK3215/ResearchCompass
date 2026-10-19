"""ResearchCompass multi-format paper export service.

Exports papers stored in a knowledge base as Markdown collection, BibTeX, RIS
(EndNote / Zotero / Mendeley compatible), CSV, or plain text.

Format design is referenced from the LitScan project ``core/exporter.py``
(MIT License, Copyright (c) 2026 LPK3215) and rewritten for the ResearchCompass
paper data model. BibTeX reuses the existing escaping and citation-key logic in
``research_paper_service`` so both export endpoints produce identical entries.
This module performs pure rendering and never issues network requests.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO
from typing import Any

from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.services.research_paper_service import _ensure_access, _paper_to_bibtex
from yuxi.storage.postgres.models_business import User

EXPORT_FORMATS = {
    "bibtex": {"ext": "bib", "media_type": "application/x-bibtex; charset=utf-8"},
    "markdown": {"ext": "md", "media_type": "text/markdown; charset=utf-8"},
    "ris": {"ext": "ris", "media_type": "application/x-research-info-systems; charset=utf-8"},
    "csv": {"ext": "csv", "media_type": "text/csv; charset=utf-8"},
    "text": {"ext": "txt", "media_type": "text/plain; charset=utf-8"},
}

CSV_FIELDS = (
    "title",
    "source",
    "year",
    "venue",
    "doi",
    "url",
    "citation_count",
    "authors",
    "keywords",
    "abstract",
)


async def export_papers_document(
    *, kb_id: str, current_user: User, paper_ids: list[str] | None, export_format: str
) -> tuple[str, bytes, str]:
    """Export papers and return (filename, content, media_type)."""
    if export_format not in EXPORT_FORMATS:
        raise ValueError(f"unsupported export format: {export_format}")
    await _ensure_access(current_user, kb_id)
    repo = AcademicPaperRepository()
    if paper_ids:
        papers = await repo.list_by_paper_ids(kb_id=kb_id, paper_ids=paper_ids)
    else:
        papers = []
        async for batch in repo.iter_export_batches(kb_id=kb_id):
            papers.extend(batch)

    format_meta = EXPORT_FORMATS[export_format]
    payload = render_papers(papers, export_format)
    return f"papers_{kb_id}.{format_meta['ext']}", payload, format_meta["media_type"]


def render_papers(papers: list[Any], export_format: str) -> bytes:
    """Render papers into the requested export format; BibTeX reuses the existing entry builder."""
    if export_format == "bibtex":
        content = "\n\n".join(_paper_to_bibtex(paper, index) for index, paper in enumerate(papers, start=1))
        return content.encode("utf-8")
    if export_format == "markdown":
        return render_papers_markdown(papers).encode("utf-8")
    if export_format == "ris":
        return render_papers_ris(papers).encode("utf-8")
    if export_format == "csv":
        return render_papers_csv(papers)
    if export_format == "text":
        return render_papers_text(papers).encode("utf-8")
    raise ValueError(f"unsupported export format: {export_format}")


def render_papers_markdown(papers: list[Any]) -> str:
    """Render a Markdown literature collection suitable for notes and project reports."""
    lines = [
        "# ResearchCompass Literature Collection",
        "",
        f"> Exported at: {datetime.now(UTC).isoformat()} | Papers: {len(papers)}",
        "",
    ]
    for index, paper in enumerate(papers, start=1):
        title = _single_line(paper.title) or "(untitled)"
        lines.extend([f"## {index}. {title}", ""])
        meta = [
            value
            for value in (
                f"`{paper.metadata_source}`" if paper.metadata_source else "",
                str(paper.publication_year) if paper.publication_year is not None else "",
                _single_line(paper.venue),
                f"{int(paper.citation_count)} citations" if paper.citation_count is not None else "",
            )
            if value
        ]
        if meta:
            lines.extend([" | ".join(meta), ""])
        authors = _join_authors(paper.authors, separator="; ")
        if authors:
            lines.extend([f"**Authors**: {authors}", ""])
        links = []
        url = _paper_url(paper)
        if url:
            links.append(f"[Source]({url})")
        if paper.doi:
            links.append(f"[DOI](https://doi.org/{_single_line(paper.doi)})")
        if links:
            lines.extend([" | ".join(links), ""])
        keywords = _join_keywords(paper.keywords)
        if keywords:
            lines.extend([f"**Keywords**: {keywords}", ""])
        abstract = _single_line(paper.abstract)
        if abstract:
            lines.extend([f"> {abstract}", ""])
    return "\n".join(lines).rstrip() + "\n"


def render_papers_ris(papers: list[Any]) -> str:
    """Render RIS records importable by EndNote, Zotero, and Mendeley."""
    blocks: list[str] = []
    for paper in papers:
        lines = [f"TY  - {'JOUR' if paper.venue else 'GEN'}"]
        title = _single_line(paper.title)
        if title:
            lines.append(f"TI  - {title}")
        for author in _author_list(paper.authors):
            lines.append(f"AU  - {author}")
        if paper.publication_year is not None:
            lines.append(f"PY  - {int(paper.publication_year)}")
        venue = _single_line(paper.venue)
        if venue:
            lines.append(f"JO  - {venue}")
        doi = _single_line(paper.doi)
        if doi:
            lines.append(f"DO  - {doi}")
        url = _paper_url(paper)
        if url:
            lines.append(f"UR  - {url}")
        for keyword in _keyword_list(paper.keywords):
            lines.append(f"KW  - {keyword}")
        abstract = _single_line(paper.abstract)
        if abstract:
            lines.append(f"AB  - {abstract}")
        lines.append("ER  - ")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_papers_csv(papers: list[Any]) -> bytes:
    """Render UTF-8 BOM CSV for spreadsheet tools and neutralize formula injection."""
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CSV_FIELDS)
    for paper in papers:
        writer.writerow(
            [
                _csv_cell(value)
                for value in (
                    paper.title,
                    paper.metadata_source,
                    paper.publication_year,
                    paper.venue,
                    paper.doi,
                    _paper_url(paper),
                    paper.citation_count,
                    _join_authors(paper.authors, separator="; "),
                    _join_keywords(paper.keywords),
                    _single_line(paper.abstract),
                )
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def render_papers_text(papers: list[Any]) -> str:
    """Render a plain text list suitable for chat or terminal output."""
    lines = []
    for paper in papers:
        title = _single_line(paper.title) or "(untitled)"
        meta = [
            value
            for value in (
                str(paper.publication_year) if paper.publication_year is not None else "",
                _single_line(paper.venue),
            )
            if value
        ]
        prefix = f" ({', '.join(meta)})" if meta else ""
        url = _paper_url(paper)
        suffix = f" - {url}" if url else ""
        lines.append(f"- {title}{prefix}{suffix}")
    return "\n".join(lines) + ("\n" if lines else "")


def _paper_url(paper: Any) -> str | None:
    """Derive a stable source link: DOI first, then arXiv, then Semantic Scholar."""
    doi = _single_line(paper.doi)
    if doi:
        return f"https://doi.org/{doi}"
    external_ids = paper.external_ids if isinstance(paper.external_ids, dict) else {}
    arxiv_id = _single_line(external_ids.get("ArXiv"))
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    s2_id = _single_line(external_ids.get("SemanticScholar"))
    if s2_id:
        return f"https://www.semanticscholar.org/paper/{s2_id}"
    return None


def _author_list(authors: Any) -> list[str]:
    return [str(author).strip() for author in authors or [] if str(author).strip()]


def _join_authors(authors: Any, *, separator: str) -> str:
    return separator.join(_author_list(authors))


def _keyword_list(keywords: Any) -> list[str]:
    return [str(keyword).strip() for keyword in keywords or [] if str(keyword).strip()]


def _join_keywords(keywords: Any) -> str:
    return ", ".join(_keyword_list(keywords))


def _single_line(value: Any) -> str:
    """Collapse multi-line fields so Markdown block quotes and RIS records stay intact."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _csv_cell(value: Any) -> str:
    """Prefix formula-like cells to block spreadsheet formula execution."""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


__all__ = [
    "EXPORT_FORMATS",
    "export_papers_document",
    "render_papers",
    "render_papers_csv",
    "render_papers_markdown",
    "render_papers_ris",
    "render_papers_text",
]
