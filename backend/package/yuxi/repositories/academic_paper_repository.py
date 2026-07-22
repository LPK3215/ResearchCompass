from __future__ import annotations

from typing import Any

from sqlalchemy import String, cast, func, or_, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import AcademicPaper


class AcademicPaperRepository:
    async def get_by_id(self, academic_paper_id: int) -> AcademicPaper | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(select(AcademicPaper).where(AcademicPaper.id == academic_paper_id))

    _writable_fields = {
        "paper_id",
        "kb_id",
        "file_id",
        "title",
        "abstract",
        "authors",
        "publication_year",
        "venue",
        "doi",
        "keywords",
        "language",
        "external_ids",
        "citation_count",
        "metadata_source",
        "metadata_status",
        "metadata_error",
        "metadata_revision",
        "indexed_revision",
    }

    async def upsert_for_file(self, *, kb_id: str, file_id: str, data: dict[str, Any]) -> AcademicPaper:
        values = {key: value for key, value in data.items() if key in self._writable_fields}
        values.update({"kb_id": kb_id, "file_id": file_id})
        if not values.get("paper_id") or not values.get("title"):
            raise ValueError("paper_id 和 title 是论文元数据必填项")

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, AcademicPaper.file_id == file_id)
            )
            paper = result.scalar_one_or_none()
            if paper is None:
                duplicate_filters = [AcademicPaper.paper_id == values["paper_id"]]
                normalized_doi = str(values.get("doi") or "").strip().casefold()
                if normalized_doi:
                    duplicate_filters.extend(
                        [
                            func.lower(func.coalesce(AcademicPaper.doi, "")) == normalized_doi,
                            func.lower(func.coalesce(AcademicPaper.external_ids["DOI"].as_string(), ""))
                            == normalized_doi,
                        ]
                    )
                for key, value in (values.get("external_ids") or {}).items():
                    normalized_value = str(value or "").strip()
                    if not normalized_value or str(key).casefold() == "doi":
                        continue
                    duplicate_filters.append(
                        func.lower(func.coalesce(AcademicPaper.external_ids[str(key)].as_string(), ""))
                        == normalized_value.casefold()
                    )
                    if str(key).casefold() in {"semanticscholar", "s2paperid"}:
                        duplicate_filters.extend(
                            [
                                func.lower(func.coalesce(AcademicPaper.external_ids["SemanticScholar"].as_string(), ""))
                                == normalized_value.casefold(),
                                func.lower(func.coalesce(AcademicPaper.external_ids["S2PaperId"].as_string(), ""))
                                == normalized_value.casefold(),
                            ]
                        )
                duplicate_result = await session.execute(
                    select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, or_(*duplicate_filters))
                )
                duplicate = duplicate_result.scalar_one_or_none()
                if duplicate is not None:
                    raise ValueError(f"论文已存在于当前知识库: {duplicate.title}")
                revision = max(int(values.get("metadata_revision") or 1), 1)
                values["metadata_revision"] = revision
                values["indexed_revision"] = revision
                paper = AcademicPaper(**values)
                session.add(paper)
            else:
                manual_metadata = paper.metadata_source == "manual"
                incoming_revision = values.get("metadata_revision")
                current_revision = int(paper.metadata_revision or 1)
                if incoming_revision is not None and int(incoming_revision) < current_revision:
                    paper.indexed_revision = max(int(paper.indexed_revision or 0), int(incoming_revision))
                else:
                    for key, value in values.items():
                        if manual_metadata and key in {
                            "title",
                            "abstract",
                            "authors",
                            "publication_year",
                            "venue",
                            "doi",
                            "keywords",
                            "language",
                            "external_ids",
                            "citation_count",
                        }:
                            continue
                        setattr(paper, key, value)
                    indexed_revision = int(incoming_revision or paper.metadata_revision or 1)
                    paper.indexed_revision = max(int(paper.indexed_revision or 0), indexed_revision)
            await session.flush()
            return paper

    async def get_by_file_id(self, *, kb_id: str, file_id: str) -> AcademicPaper | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, AcademicPaper.file_id == file_id)
            )
            return result.scalar_one_or_none()

    async def get_by_paper_id(self, *, kb_id: str, paper_id: str) -> AcademicPaper | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, AcademicPaper.paper_id == paper_id)
            )
            return result.scalar_one_or_none()

    async def find_duplicate_for_import(
        self,
        *,
        kb_id: str,
        paper_id: str,
        doi: str | None,
        external_ids: dict[str, Any] | None,
    ) -> AcademicPaper | None:
        """Find an existing paper by any stable external identity in one knowledge base."""
        identity_filters = [AcademicPaper.paper_id == paper_id]
        normalized_doi = str(doi or "").strip().casefold()
        if normalized_doi:
            identity_filters.extend(
                [
                    func.lower(func.coalesce(AcademicPaper.doi, "")) == normalized_doi,
                    func.lower(func.coalesce(AcademicPaper.external_ids["DOI"].as_string(), "")) == normalized_doi,
                ]
            )
        for key, value in (external_ids or {}).items():
            normalized_value = str(value or "").strip()
            if not normalized_value or key.casefold() == "doi":
                continue
            identity_filters.append(
                func.lower(func.coalesce(AcademicPaper.external_ids[str(key)].as_string(), ""))
                == normalized_value.casefold()
            )
            if key.casefold() in {"semanticscholar", "s2paperid"}:
                identity_filters.extend(
                    [
                        func.lower(func.coalesce(AcademicPaper.external_ids["SemanticScholar"].as_string(), ""))
                        == normalized_value.casefold(),
                        func.lower(func.coalesce(AcademicPaper.external_ids["S2PaperId"].as_string(), ""))
                        == normalized_value.casefold(),
                    ]
                )
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, or_(*identity_filters)).limit(1)
            )
            return result.scalar_one_or_none()

    async def update(self, *, kb_id: str, paper_id: str, data: dict[str, Any]) -> AcademicPaper | None:
        values = {key: value for key, value in data.items() if key in self._writable_fields}
        values.pop("paper_id", None)
        values.pop("kb_id", None)
        values.pop("file_id", None)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, AcademicPaper.paper_id == paper_id)
            )
            paper = result.scalar_one_or_none()
            if paper is None:
                return None
            for key, value in values.items():
                setattr(paper, key, value)
            await session.flush()
            return paper

    async def update_metadata(self, *, kb_id: str, paper_id: str, data: dict[str, Any]) -> AcademicPaper | None:
        values = {key: value for key, value in data.items() if key in self._writable_fields}
        values.pop("paper_id", None)
        values.pop("kb_id", None)
        values.pop("file_id", None)
        values.pop("metadata_revision", None)
        values.pop("indexed_revision", None)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper)
                .where(AcademicPaper.kb_id == kb_id, AcademicPaper.paper_id == paper_id)
                .with_for_update()
            )
            paper = result.scalar_one_or_none()
            if paper is None:
                return None
            for key, value in values.items():
                setattr(paper, key, value)
            paper.metadata_revision = int(paper.metadata_revision or 1) + 1
            await session.flush()
            return paper

    async def complete_reindex(self, *, kb_id: str, paper_id: str, revision: int) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper)
                .where(AcademicPaper.kb_id == kb_id, AcademicPaper.paper_id == paper_id)
                .with_for_update()
            )
            paper = result.scalar_one_or_none()
            if paper is None:
                return False
            paper.indexed_revision = max(int(paper.indexed_revision or 0), revision)
            if int(paper.metadata_revision or 1) != revision:
                return False
            paper.metadata_status = "verified"
            paper.metadata_error = None
            return True

    async def fail_reindex(self, *, kb_id: str, paper_id: str, revision: int, error: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper)
                .where(AcademicPaper.kb_id == kb_id, AcademicPaper.paper_id == paper_id)
                .with_for_update()
            )
            paper = result.scalar_one_or_none()
            if paper is None or int(paper.metadata_revision or 1) != revision:
                return
            paper.metadata_status = "sync_failed"
            paper.metadata_error = error

    async def list_by_file_ids(self, *, kb_id: str, file_ids: list[str]) -> list[AcademicPaper]:
        if not file_ids:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, AcademicPaper.file_id.in_(file_ids))
            )
            papers_by_file = {paper.file_id: paper for paper in result.scalars().all()}
            return [papers_by_file[file_id] for file_id in file_ids if file_id in papers_by_file]

    async def list_file_ids_by_filters(
        self, *, kb_id: str, year_from: int | None = None, year_to: int | None = None
    ) -> list[str]:
        filters = [
            AcademicPaper.kb_id == kb_id,
            AcademicPaper.indexed_revision >= AcademicPaper.metadata_revision,
            AcademicPaper.metadata_status.in_(["extracted", "verified"]),
        ]
        if year_from is not None:
            filters.append(AcademicPaper.publication_year >= year_from)
        if year_to is not None:
            filters.append(AcademicPaper.publication_year <= year_to)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(AcademicPaper.file_id).where(*filters))
            return list(result.scalars().all())

    async def list_pending_reindex(self) -> list[AcademicPaper]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper).where(AcademicPaper.metadata_status == "pending_reindex")
            )
            return list(result.scalars().all())

    async def list_for_graph_sync(self, *, kb_id: str, paper_ids: list[str] | None = None) -> list[AcademicPaper]:
        filters = [
            AcademicPaper.kb_id == kb_id,
            AcademicPaper.indexed_revision >= AcademicPaper.metadata_revision,
            AcademicPaper.metadata_status.in_(["extracted", "verified"]),
        ]
        if paper_ids:
            filters.append(AcademicPaper.paper_id.in_(paper_ids))
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(AcademicPaper).where(*filters).order_by(AcademicPaper.id.asc()))
            return list(result.scalars().all())

    async def update_external_metadata(
        self,
        *,
        kb_id: str,
        paper_id: str,
        external_ids: dict[str, Any],
        citation_count: int | None,
    ) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, AcademicPaper.paper_id == paper_id)
            )
            paper = result.scalar_one_or_none()
            if paper is None:
                return
            paper.external_ids = external_ids
            paper.citation_count = citation_count

    async def list_by_kb_id(
        self,
        *,
        kb_id: str,
        query: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[AcademicPaper], int]:
        filters = [AcademicPaper.kb_id == kb_id]
        normalized_query = (query or "").strip()
        if normalized_query:
            search = f"%{normalized_query.casefold()}%"
            filters.append(
                or_(
                    func.lower(AcademicPaper.title).like(search),
                    func.lower(func.coalesce(AcademicPaper.doi, "")).like(search),
                    func.lower(func.coalesce(AcademicPaper.venue, "")).like(search),
                    func.lower(cast(AcademicPaper.authors, String)).like(search),
                )
            )
        if year_from is not None:
            filters.append(AcademicPaper.publication_year >= year_from)
        if year_to is not None:
            filters.append(AcademicPaper.publication_year <= year_to)

        normalized_offset = max(int(offset or 0), 0)
        normalized_limit = min(max(int(limit or 50), 1), 200)
        async with pg_manager.get_async_session_context() as session:
            total_result = await session.execute(select(func.count()).select_from(AcademicPaper).where(*filters))
            result = await session.execute(
                select(AcademicPaper)
                .where(*filters)
                .order_by(AcademicPaper.publication_year.desc().nullslast(), AcademicPaper.id.desc())
                .offset(normalized_offset)
                .limit(normalized_limit)
            )
            return list(result.scalars().all()), int(total_result.scalar_one() or 0)

    async def list_trend_records(
        self,
        *,
        kb_id: str,
        year_from: int | None,
        year_to: int | None,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[int | None, list[str], int | None]], int]:
        filters = [
            AcademicPaper.kb_id == kb_id,
            AcademicPaper.indexed_revision >= AcademicPaper.metadata_revision,
            AcademicPaper.metadata_status.in_(["extracted", "verified"]),
        ]
        if year_from is not None:
            filters.append(AcademicPaper.publication_year >= year_from)
        if year_to is not None:
            filters.append(AcademicPaper.publication_year <= year_to)
        normalized_offset = max(int(offset), 0)
        normalized_limit = min(max(int(limit), 1), 50_000)
        async with pg_manager.get_async_session_context() as session:
            total = int(await session.scalar(select(func.count()).select_from(AcademicPaper).where(*filters)) or 0)
            result = await session.execute(
                select(AcademicPaper.publication_year, AcademicPaper.keywords, AcademicPaper.citation_count)
                .where(*filters)
                .order_by(AcademicPaper.publication_year.asc().nullslast(), AcademicPaper.id.asc())
                .offset(normalized_offset)
                .limit(normalized_limit)
            )
            rows = []
            for year, keywords, citation_count in result.all():
                normalized_keywords = [
                    str(keyword).strip()
                    for keyword in (keywords if isinstance(keywords, list) else [])
                    if str(keyword).strip()
                ]
                rows.append((year, normalized_keywords, citation_count))
            return rows, total
