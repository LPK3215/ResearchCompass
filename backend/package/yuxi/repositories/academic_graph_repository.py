from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    AcademicAuthor,
    AcademicCitation,
    AcademicGraphPaper,
    AcademicGraphPaperAuthor,
    AcademicGraphPaperTopic,
    AcademicGraphSyncRun,
    AcademicMetadataConflict,
    AcademicPaper,
    AcademicTopic,
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AcademicGraphRepository:
    async def create_sync_run(
        self,
        *,
        run_id: str,
        kb_id: str,
        uid: str,
        requested_paper_ids: list[str],
        sync_config: dict[str, Any],
    ) -> AcademicGraphSyncRun:
        record = AcademicGraphSyncRun(
            run_id=run_id,
            kb_id=kb_id,
            uid=uid,
            status="pending",
            requested_paper_ids=requested_paper_ids,
            sync_config=sync_config,
        )
        async with pg_manager.get_async_session_context() as session:
            session.add(record)
            await session.flush()
        return record

    async def get_sync_run(self, run_id: str) -> AcademicGraphSyncRun | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(AcademicGraphSyncRun).where(AcademicGraphSyncRun.run_id == run_id))
            return result.scalar_one_or_none()

    async def list_recoverable_sync_runs(self) -> list[AcademicGraphSyncRun]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicGraphSyncRun).where(AcademicGraphSyncRun.status.in_(["pending", "running"]))
            )
            return list(result.scalars().all())

    async def update_sync_run(self, run_id: str, data: dict[str, Any]) -> AcademicGraphSyncRun | None:
        allowed = {
            "status",
            "processed_papers",
            "graph_papers",
            "citations",
            "authors",
            "topics",
            "conflict_count",
            "error_type",
            "error_message",
            "started_at",
            "completed_at",
        }
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(AcademicGraphSyncRun).where(AcademicGraphSyncRun.run_id == run_id))
            record = result.scalar_one_or_none()
            if record is None:
                return None
            for key, value in data.items():
                if key in allowed:
                    setattr(record, key, value)
            await session.flush()
            return record

    async def upsert_graph_paper(self, data: dict[str, Any]) -> str:
        values = dict(data)
        values["last_synced_at"] = _utc_now()
        async with pg_manager.get_async_session_context() as session:
            stmt = insert(AcademicGraphPaper).values(values)
            await session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_academic_graph_papers_identity",
                    set_={
                        "academic_paper_id": func.coalesce(
                            AcademicGraphPaper.academic_paper_id, stmt.excluded.academic_paper_id
                        ),
                        "semantic_scholar_id": func.coalesce(
                            stmt.excluded.semantic_scholar_id, AcademicGraphPaper.semantic_scholar_id
                        ),
                        "external_ids": stmt.excluded.external_ids,
                        "title": stmt.excluded.title,
                        "abstract": stmt.excluded.abstract,
                        "publication_year": stmt.excluded.publication_year,
                        "venue": stmt.excluded.venue,
                        "citation_count": stmt.excluded.citation_count,
                        "reference_count": stmt.excluded.reference_count,
                        "influential_citation_count": stmt.excluded.influential_citation_count,
                        "is_open_access": stmt.excluded.is_open_access,
                        "open_access_url": stmt.excluded.open_access_url,
                        "is_library_paper": AcademicGraphPaper.is_library_paper | stmt.excluded.is_library_paper,
                        "source": stmt.excluded.source,
                        "last_synced_at": stmt.excluded.last_synced_at,
                        "updated_at": func.now(),
                    },
                )
            )
            result = await session.execute(
                select(AcademicGraphPaper.graph_paper_id).where(
                    AcademicGraphPaper.kb_id == values["kb_id"],
                    AcademicGraphPaper.identity_key == values["identity_key"],
                )
            )
            return str(result.scalar_one())

    async def validate_local_identity(
        self, *, kb_id: str, academic_paper_id: int, identity_key: str
    ) -> tuple[bool, str | None]:
        async with pg_manager.get_async_session_context() as session:
            local = await session.scalar(
                select(AcademicGraphPaper).where(
                    AcademicGraphPaper.kb_id == kb_id,
                    AcademicGraphPaper.academic_paper_id == academic_paper_id,
                )
            )
            if local is not None and local.identity_key != identity_key:
                return False, f"本地论文已绑定图谱身份 {local.identity_key}，新身份为 {identity_key}"
            identity_owner = await session.scalar(
                select(AcademicGraphPaper).where(
                    AcademicGraphPaper.kb_id == kb_id,
                    AcademicGraphPaper.identity_key == identity_key,
                )
            )
            if (
                identity_owner is not None
                and identity_owner.academic_paper_id is not None
                and identity_owner.academic_paper_id != academic_paper_id
            ):
                return False, "该图谱身份已经绑定另一篇论文库论文"
            return True, None

    async def upsert_authors(self, *, graph_paper_id: str, authors: list[dict[str, Any]]) -> int:
        if not authors:
            return 0
        async with pg_manager.get_async_session_context() as session:
            for order, author in enumerate(authors):
                stmt = insert(AcademicAuthor).values(author)
                await session.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_academic_authors_identity",
                        set_={
                            "name": stmt.excluded.name,
                            "semantic_scholar_id": func.coalesce(
                                stmt.excluded.semantic_scholar_id, AcademicAuthor.semantic_scholar_id
                            ),
                            "external_ids": stmt.excluded.external_ids,
                            "updated_at": func.now(),
                        },
                    )
                )
                author_id = await session.scalar(
                    select(AcademicAuthor.author_id).where(
                        AcademicAuthor.kb_id == author["kb_id"],
                        AcademicAuthor.identity_key == author["identity_key"],
                    )
                )
                await session.execute(
                    insert(AcademicGraphPaperAuthor)
                    .values(graph_paper_id=graph_paper_id, author_id=author_id, author_order=order)
                    .on_conflict_do_update(
                        constraint="uq_academic_graph_paper_authors_pair",
                        set_={"author_order": order},
                    )
                )
        return len(authors)

    async def upsert_topics(self, *, graph_paper_id: str, topics: list[dict[str, Any]]) -> int:
        if not topics:
            return 0
        async with pg_manager.get_async_session_context() as session:
            for topic in topics:
                stmt = insert(AcademicTopic).values(topic)
                await session.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_academic_topics_identity",
                        set_={"name": stmt.excluded.name, "category": stmt.excluded.category, "updated_at": func.now()},
                    )
                )
                topic_id = await session.scalar(
                    select(AcademicTopic.topic_id).where(
                        AcademicTopic.kb_id == topic["kb_id"],
                        AcademicTopic.normalized_name == topic["normalized_name"],
                    )
                )
                await session.execute(
                    insert(AcademicGraphPaperTopic)
                    .values(graph_paper_id=graph_paper_id, topic_id=topic_id)
                    .on_conflict_do_nothing(constraint="uq_academic_graph_paper_topics_pair")
                )
        return len(topics)

    async def upsert_citation(self, data: dict[str, Any]) -> None:
        values = {**data, "last_synced_at": _utc_now()}
        async with pg_manager.get_async_session_context() as session:
            stmt = insert(AcademicCitation).values(values)
            await session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_academic_citations_edge",
                    set_={
                        "contexts": stmt.excluded.contexts,
                        "intents": stmt.excluded.intents,
                        "is_influential": stmt.excluded.is_influential,
                        "source": stmt.excluded.source,
                        "last_synced_at": stmt.excluded.last_synced_at,
                        "updated_at": func.now(),
                    },
                )
            )

    async def add_conflict(self, data: dict[str, Any]) -> None:
        async with pg_manager.get_async_session_context() as session:
            session.add(AcademicMetadataConflict(**data))

    async def list_conflicts(
        self,
        *,
        run_id: str,
        resolution_status: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[AcademicMetadataConflict], int]:
        filters = [AcademicMetadataConflict.run_id == run_id]
        if resolution_status:
            filters.append(AcademicMetadataConflict.resolution_status == resolution_status)
        async with pg_manager.get_async_session_context() as session:
            total = int(
                await session.scalar(
                    select(func.count()).select_from(AcademicMetadataConflict).where(*filters)
                )
                or 0
            )
            result = await session.execute(
                select(AcademicMetadataConflict)
                .where(*filters)
                .order_by(AcademicMetadataConflict.created_at.asc(), AcademicMetadataConflict.id.asc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all()), total

    async def resolve_seed_papers(self, *, kb_id: str, paper_scores: dict[str, float]) -> dict[str, float]:
        if not paper_scores:
            return {}
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicGraphPaper.graph_paper_id, AcademicPaper.paper_id)
                .join(AcademicPaper, AcademicGraphPaper.academic_paper_id == AcademicPaper.id)
                .where(
                    AcademicGraphPaper.kb_id == kb_id,
                    AcademicPaper.kb_id == kb_id,
                    AcademicPaper.paper_id.in_(paper_scores),
                )
            )
            return {
                str(graph_paper_id): float(paper_scores[str(paper_id)])
                for graph_paper_id, paper_id in result.all()
            }

    async def list_library_paper_ids(
        self, *, kb_id: str, graph_paper_ids: list[str]
    ) -> dict[str, str]:
        if not graph_paper_ids:
            return {}
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicGraphPaper.graph_paper_id, AcademicPaper.paper_id)
                .join(AcademicPaper, AcademicGraphPaper.academic_paper_id == AcademicPaper.id)
                .where(
                    AcademicGraphPaper.kb_id == kb_id,
                    AcademicPaper.kb_id == kb_id,
                    AcademicGraphPaper.graph_paper_id.in_(graph_paper_ids),
                )
            )
            return {str(graph_paper_id): str(paper_id) for graph_paper_id, paper_id in result.all()}

    async def get_graph(self, *, kb_id: str, center_paper_id: str | None, limit: int) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 500)
        async with pg_manager.get_async_session_context() as session:
            citation_filter = [AcademicCitation.kb_id == kb_id]
            if center_paper_id:
                citation_filter.append(
                    or_(
                        AcademicCitation.citing_paper_id == center_paper_id,
                        AcademicCitation.cited_paper_id == center_paper_id,
                    )
                )
            citations = list(
                (
                    await session.execute(
                        select(AcademicCitation).where(*citation_filter).order_by(AcademicCitation.id.desc()).limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            paper_ids = {paper_id for edge in citations for paper_id in (edge.citing_paper_id, edge.cited_paper_id)}
            if center_paper_id:
                paper_ids.add(center_paper_id)
            papers = list(
                (
                    await session.execute(
                        select(AcademicGraphPaper).where(
                            AcademicGraphPaper.kb_id == kb_id,
                            AcademicGraphPaper.graph_paper_id.in_(paper_ids),
                        )
                    )
                )
                .scalars()
                .all()
            ) if paper_ids else []
            return {"papers": papers, "citations": citations}

    async def find_two_hop_relations(
        self, *, kb_id: str, source_graph_paper_id: str, limit: int
    ) -> dict[str, Any] | None:
        """Find explainable two-hop citation associations around one graph paper.

        The first hop is restricted to the source paper and the second hop to its
        neighbours.  This keeps inference bounded while preserving the direction
        of every citation edge for the explanation returned to callers.
        """
        limit = min(max(int(limit), 1), 100)
        edge_limit = min(max(limit * 100, 200), 5000)
        async with pg_manager.get_async_session_context() as session:
            source = await session.scalar(
                select(AcademicGraphPaper).where(
                    AcademicGraphPaper.kb_id == kb_id,
                    AcademicGraphPaper.graph_paper_id == source_graph_paper_id,
                )
            )
            if source is None:
                return None

            first_edges = list(
                (
                    await session.execute(
                        select(AcademicCitation)
                        .where(
                            AcademicCitation.kb_id == kb_id,
                            or_(
                                AcademicCitation.citing_paper_id == source_graph_paper_id,
                                AcademicCitation.cited_paper_id == source_graph_paper_id,
                            ),
                        )
                        .order_by(AcademicCitation.id.asc())
                        .limit(edge_limit)
                    )
                )
                .scalars()
                .all()
            )
            neighbours = {
                edge.cited_paper_id if edge.citing_paper_id == source_graph_paper_id else edge.citing_paper_id
                for edge in first_edges
            }
            if not neighbours:
                return {"source": source, "papers": {}, "paths": []}

            second_edges = list(
                (
                    await session.execute(
                        select(AcademicCitation)
                        .where(
                            AcademicCitation.kb_id == kb_id,
                            or_(
                                AcademicCitation.citing_paper_id.in_(neighbours),
                                AcademicCitation.cited_paper_id.in_(neighbours),
                            ),
                        )
                        .order_by(AcademicCitation.id.asc())
                        .limit(edge_limit)
                    )
                )
                .scalars()
                .all()
            )

            edges_by_node: dict[str, list[AcademicCitation]] = {}
            for edge in [*first_edges, *second_edges]:
                edges_by_node.setdefault(edge.citing_paper_id, []).append(edge)
                edges_by_node.setdefault(edge.cited_paper_id, []).append(edge)

            paths: list[dict[str, Any]] = []
            paper_ids = {source_graph_paper_id}
            for first_edge in first_edges:
                middle_id = (
                    first_edge.cited_paper_id
                    if first_edge.citing_paper_id == source_graph_paper_id
                    else first_edge.citing_paper_id
                )
                first_direction = (
                    "outgoing" if first_edge.citing_paper_id == source_graph_paper_id else "incoming"
                )
                for second_edge in edges_by_node.get(middle_id, []):
                    target_id = (
                        second_edge.cited_paper_id
                        if second_edge.citing_paper_id == middle_id
                        else second_edge.citing_paper_id
                    )
                    if target_id in {source_graph_paper_id, middle_id}:
                        continue
                    second_direction = "outgoing" if second_edge.citing_paper_id == middle_id else "incoming"
                    directions = (first_direction, second_direction)
                    relation_type = {
                        ("outgoing", "outgoing"): "citation_chain",
                        ("outgoing", "incoming"): "shared_reference",
                        ("incoming", "outgoing"): "shared_citation",
                        ("incoming", "incoming"): "reverse_citation_chain",
                    }[directions]
                    path = {
                        "target_graph_paper_id": target_id,
                        "intermediate_graph_paper_id": middle_id,
                        "relation_type": relation_type,
                        "edges": [first_edge, second_edge],
                    }
                    paths.append(path)
                    paper_ids.update((middle_id, target_id))

            papers = {
                paper.graph_paper_id: paper
                for paper in (
                    await session.execute(
                        select(AcademicGraphPaper).where(
                            AcademicGraphPaper.kb_id == kb_id,
                            AcademicGraphPaper.graph_paper_id.in_(paper_ids),
                        )
                    )
                )
                .scalars()
                .all()
            }
            return {"source": source, "papers": papers, "paths": paths}

    async def counts(self, kb_id: str) -> dict[str, int]:
        async with pg_manager.get_async_session_context() as session:
            async def count(model) -> int:
                return int(
                    await session.scalar(select(func.count()).select_from(model).where(model.kb_id == kb_id)) or 0
                )

            return {
                "papers": await count(AcademicGraphPaper),
                "citations": await count(AcademicCitation),
                "authors": await count(AcademicAuthor),
                "topics": await count(AcademicTopic),
            }

    async def citation_trend(
        self,
        *,
        kb_id: str,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[dict[str, int]]:
        """按引用论文的发表年份统计 CITES 边。

        这里的年份口径与论文趋势页保持一致：年份筛选作用于发起引用的论文，
        因而用户选择时间窗口后，论文趋势和引用网络趋势不会混用不同样本范围。
        """
        filters = [
            AcademicGraphPaper.kb_id == kb_id,
            AcademicGraphPaper.is_library_paper.is_(True),
            AcademicCitation.kb_id == kb_id,
            AcademicGraphPaper.publication_year.is_not(None),
        ]
        if year_from is not None:
            filters.append(AcademicGraphPaper.publication_year >= year_from)
        if year_to is not None:
            filters.append(AcademicGraphPaper.publication_year <= year_to)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicGraphPaper.publication_year, func.count(AcademicCitation.id))
                .join(
                    AcademicCitation,
                    AcademicCitation.citing_paper_id == AcademicGraphPaper.graph_paper_id,
                )
                .where(*filters)
                .group_by(AcademicGraphPaper.publication_year)
                .order_by(AcademicGraphPaper.publication_year.asc())
            )
            return [{"year": int(year), "citations": int(count)} for year, count in result.all()]

    async def get_graph_paper_by_library_paper(
        self, *, kb_id: str, academic_paper_id: int
    ) -> AcademicGraphPaper | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicGraphPaper).where(
                    AcademicGraphPaper.kb_id == kb_id,
                    AcademicGraphPaper.academic_paper_id == academic_paper_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_neighbor_citations(
        self, *, kb_id: str, graph_paper_id: str, limit: int = 40
    ) -> dict[str, Any]:
        """Return one-hop CITES neighbors with paper metadata for analysis context."""
        limit = min(max(int(limit), 1), 100)
        async with pg_manager.get_async_session_context() as session:
            edges = list(
                (
                    await session.execute(
                        select(AcademicCitation)
                        .where(
                            AcademicCitation.kb_id == kb_id,
                            or_(
                                AcademicCitation.citing_paper_id == graph_paper_id,
                                AcademicCitation.cited_paper_id == graph_paper_id,
                            ),
                        )
                        .order_by(AcademicCitation.id.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            paper_ids = {graph_paper_id}
            for edge in edges:
                paper_ids.add(edge.citing_paper_id)
                paper_ids.add(edge.cited_paper_id)
            papers = {
                paper.graph_paper_id: paper
                for paper in (
                    await session.execute(
                        select(AcademicGraphPaper).where(
                            AcademicGraphPaper.kb_id == kb_id,
                            AcademicGraphPaper.graph_paper_id.in_(paper_ids),
                        )
                    )
                )
                .scalars()
                .all()
            }
            return {"center": papers.get(graph_paper_id), "papers": papers, "citations": edges}

    async def clear_kb(self, kb_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            for model in (
                AcademicMetadataConflict,
                AcademicGraphPaper,
                AcademicAuthor,
                AcademicTopic,
                AcademicCitation,
                AcademicGraphPaperAuthor,
                AcademicGraphPaperTopic,
            ):
                if hasattr(model, "kb_id"):
                    await session.execute(delete(model).where(model.kb_id == kb_id))
            # 删除关联表的记录（junction tables）
            await session.execute(
                delete(AcademicGraphPaperAuthor).where(
                    AcademicGraphPaperAuthor.graph_paper_id.in_(
                        select(AcademicGraphPaper.graph_paper_id).where(AcademicGraphPaper.kb_id == kb_id)
                    )
                )
            )
            await session.execute(
                delete(AcademicGraphPaperTopic).where(
                    AcademicGraphPaperTopic.graph_paper_id.in_(
                        select(AcademicGraphPaper.graph_paper_id).where(AcademicGraphPaper.kb_id == kb_id)
                    )
                )
            )


__all__ = ["AcademicGraphRepository"]
