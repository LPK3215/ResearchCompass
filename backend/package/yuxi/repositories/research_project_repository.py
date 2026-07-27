from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    AcademicPaper,
    AcademicPaperAnalysisRun,
    EvaluationExperiment,
    ResearchProject,
    ResearchProjectActivity,
    ResearchProjectAsset,
    ResearchSearchRun,
    ResearchSynthesisRun,
)


ASSET_TYPES = {"paper", "search_run", "synthesis_run", "analysis_run", "evaluation_experiment"}


def build_project_activity(
    project_id: str,
    activity_type: str,
    *,
    asset_type: str | None = None,
    reference_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> ResearchProjectActivity:
    normalized_payload = {}
    for key, value in (payload or {}).items():
        normalized_payload[key] = value.isoformat() if isinstance(value, (date, datetime)) else value
    return ResearchProjectActivity(
        activity_id=uuid.uuid4().hex,
        project_id=project_id,
        activity_type=activity_type,
        asset_type=asset_type,
        reference_id=reference_id,
        payload=normalized_payload,
    )


class ResearchProjectRepository:
    async def create(self, values: dict[str, Any]) -> ResearchProject:
        record = ResearchProject(**values)
        async with pg_manager.get_async_session_context() as session:
            session.add(record)
            session.add(build_project_activity(record.project_id, "project_created", payload={"title": record.title}))
            await session.flush()
        return record

    async def get(self, project_id: str, *, uid: str) -> ResearchProject | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(ResearchProject).where(
                    ResearchProject.project_id == project_id,
                    ResearchProject.uid == uid,
                )
            )

    async def list_for_user(
        self,
        *,
        uid: str,
        kb_id: str | None,
        status: str | None,
        query: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[ResearchProject], int, dict[str, dict[str, int]]]:
        filters = [ResearchProject.uid == uid]
        if kb_id:
            filters.append(ResearchProject.kb_id == kb_id)
        if status:
            filters.append(ResearchProject.status == status)
        if query:
            pattern = f"%{query}%"
            filters.append(
                or_(
                    ResearchProject.title.ilike(pattern),
                    ResearchProject.research_question.ilike(pattern),
                    ResearchProject.description.ilike(pattern),
                )
            )
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count()).select_from(ResearchProject).where(*filters))
            result = await session.execute(
                select(ResearchProject)
                .where(*filters)
                .order_by(ResearchProject.updated_at.desc(), ResearchProject.id.desc())
                .offset(offset)
                .limit(limit)
            )
            projects = list(result.scalars().all())
            counts = await self._asset_counts(session, [project.project_id for project in projects])
            return projects, int(total or 0), counts

    async def update(
        self,
        project_id: str,
        *,
        uid: str,
        values: dict[str, Any],
        activity_type: str,
    ) -> ResearchProject | None:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(ResearchProject).where(
                    ResearchProject.project_id == project_id,
                    ResearchProject.uid == uid,
                )
            )
            if record is None:
                return None
            for key, value in values.items():
                setattr(record, key, value)
            session.add(build_project_activity(project_id, activity_type, payload=values))
            await session.flush()
            return record

    async def delete(self, project_id: str, *, uid: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(ResearchProject).where(
                    ResearchProject.project_id == project_id,
                    ResearchProject.uid == uid,
                )
            )
            return bool(result.rowcount)

    async def get_asset_counts(self, project_id: str) -> dict[str, int]:
        async with pg_manager.get_async_session_context() as session:
            counts = await self._asset_counts(session, [project_id])
            return counts[project_id]

    async def list_assets(
        self,
        *,
        project_id: str,
        asset_type: str | None,
        query: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[ResearchProjectAsset], int]:
        filters = [ResearchProjectAsset.project_id == project_id]
        if asset_type:
            filters.append(ResearchProjectAsset.asset_type == asset_type)
        if query:
            pattern = f"%{query}%"
            filters.append(
                or_(
                    ResearchProjectAsset.title_snapshot.ilike(pattern),
                    ResearchProjectAsset.summary_snapshot.ilike(pattern),
                    ResearchProjectAsset.notes.ilike(pattern),
                )
            )
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count()).select_from(ResearchProjectAsset).where(*filters))
            result = await session.execute(
                select(ResearchProjectAsset)
                .where(*filters)
                .order_by(ResearchProjectAsset.added_at.desc(), ResearchProjectAsset.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all()), int(total or 0)

    async def add_assets(self, project_id: str, assets: list[dict[str, Any]]) -> list[ResearchProjectAsset]:
        records = [ResearchProjectAsset(asset_id=uuid.uuid4().hex, project_id=project_id, **asset) for asset in assets]
        async with pg_manager.get_async_session_context() as session:
            session.add_all(records)
            for record in records:
                session.add(
                    build_project_activity(
                        project_id,
                        "asset_added",
                        asset_type=record.asset_type,
                        reference_id=record.reference_id,
                        payload={"title": record.title_snapshot},
                    )
                )
            await session.flush()
        return records

    async def get_asset(self, project_id: str, asset_id: str) -> ResearchProjectAsset | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(ResearchProjectAsset).where(
                    ResearchProjectAsset.project_id == project_id,
                    ResearchProjectAsset.asset_id == asset_id,
                )
            )

    async def update_asset_notes(
        self,
        project_id: str,
        asset_id: str,
        *,
        notes: str | None,
    ) -> ResearchProjectAsset | None:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(ResearchProjectAsset).where(
                    ResearchProjectAsset.project_id == project_id,
                    ResearchProjectAsset.asset_id == asset_id,
                )
            )
            if record is None:
                return None
            record.notes = notes
            session.add(
                build_project_activity(
                    project_id,
                    "asset_notes_updated",
                    asset_type=record.asset_type,
                    reference_id=record.reference_id,
                    payload={"title": record.title_snapshot},
                )
            )
            await session.flush()
            return record

    async def remove_asset(self, project_id: str, asset_id: str) -> ResearchProjectAsset | None:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(ResearchProjectAsset).where(
                    ResearchProjectAsset.project_id == project_id,
                    ResearchProjectAsset.asset_id == asset_id,
                )
            )
            if record is None:
                return None
            session.add(
                build_project_activity(
                    project_id,
                    "asset_removed",
                    asset_type=record.asset_type,
                    reference_id=record.reference_id,
                    payload={"title": record.title_snapshot},
                )
            )
            await session.delete(record)
            return record

    async def list_activities(self, project_id: str, *, limit: int) -> list[ResearchProjectActivity]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchProjectActivity)
                .where(ResearchProjectActivity.project_id == project_id)
                .order_by(ResearchProjectActivity.created_at.desc(), ResearchProjectActivity.id.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def list_all_assets(self, project_id: str) -> list[ResearchProjectAsset]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchProjectAsset)
                .where(ResearchProjectAsset.project_id == project_id)
                .order_by(ResearchProjectAsset.asset_type, ResearchProjectAsset.added_at.desc())
            )
            return list(result.scalars().all())

    async def get_linked_reference_ids(self, project_id: str, asset_type: str) -> set[str]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchProjectAsset.reference_id).where(
                    ResearchProjectAsset.project_id == project_id,
                    ResearchProjectAsset.asset_type == asset_type,
                )
            )
            return set(result.scalars().all())

    async def list_candidates(
        self,
        *,
        project_id: str,
        kb_id: str,
        uid: str,
        asset_type: str,
        query: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        linked = await self.get_linked_reference_ids(project_id, asset_type)
        async with pg_manager.get_async_session_context() as session:
            statement, count_statement = self._candidate_statements(
                kb_id=kb_id,
                uid=uid,
                asset_type=asset_type,
                query=query,
            )
            total = int(await session.scalar(count_statement) or 0)
            result = await session.execute(statement.offset(offset).limit(limit))
            candidates = [self._serialize_candidate(asset_type, row) for row in result.all()]
        for candidate in candidates:
            candidate["linked"] = candidate["reference_id"] in linked
        return candidates, total

    async def get_candidate(
        self,
        *,
        kb_id: str,
        uid: str,
        asset_type: str,
        reference_id: str,
    ) -> dict[str, Any] | None:
        statement, _ = self._candidate_statements(kb_id=kb_id, uid=uid, asset_type=asset_type, query=None)
        identity = {
            "paper": AcademicPaper.paper_id,
            "search_run": ResearchSearchRun.run_id,
            "synthesis_run": ResearchSynthesisRun.run_id,
            "analysis_run": AcademicPaperAnalysisRun.run_id,
            "evaluation_experiment": EvaluationExperiment.experiment_id,
        }[asset_type]
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(statement.where(identity == reference_id))
            row = result.first()
            return self._serialize_candidate(asset_type, row) if row else None

    async def get_candidates(
        self,
        *,
        kb_id: str,
        uid: str,
        asset_type: str,
        reference_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not reference_ids:
            return {}
        identity = {
            "paper": AcademicPaper.paper_id,
            "search_run": ResearchSearchRun.run_id,
            "synthesis_run": ResearchSynthesisRun.run_id,
            "analysis_run": AcademicPaperAnalysisRun.run_id,
            "evaluation_experiment": EvaluationExperiment.experiment_id,
        }[asset_type]
        statement, _ = self._candidate_statements(kb_id=kb_id, uid=uid, asset_type=asset_type, query=None)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(statement.where(identity.in_(reference_ids)))
            candidates = [self._serialize_candidate(asset_type, row) for row in result.all()]
        return {candidate["reference_id"]: candidate for candidate in candidates}

    async def available_reference_ids(
        self,
        *,
        kb_id: str,
        uid: str,
        asset_type: str,
        reference_ids: list[str],
    ) -> set[str]:
        if not reference_ids:
            return set()
        identity = {
            "paper": AcademicPaper.paper_id,
            "search_run": ResearchSearchRun.run_id,
            "synthesis_run": ResearchSynthesisRun.run_id,
            "analysis_run": AcademicPaperAnalysisRun.run_id,
            "evaluation_experiment": EvaluationExperiment.experiment_id,
        }[asset_type]
        statement, _ = self._candidate_statements(kb_id=kb_id, uid=uid, asset_type=asset_type, query=None)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(statement.where(identity.in_(reference_ids)))
            return {self._serialize_candidate(asset_type, row)["reference_id"] for row in result.all()}

    @staticmethod
    async def _asset_counts(session, project_ids: list[str]) -> dict[str, dict[str, int]]:
        counts = {
            project_id: {**{asset_type: 0 for asset_type in ASSET_TYPES}, "total": 0} for project_id in project_ids
        }
        if not project_ids:
            return counts
        result = await session.execute(
            select(
                ResearchProjectAsset.project_id,
                ResearchProjectAsset.asset_type,
                func.count(ResearchProjectAsset.id),
            )
            .where(ResearchProjectAsset.project_id.in_(project_ids))
            .group_by(ResearchProjectAsset.project_id, ResearchProjectAsset.asset_type)
        )
        for project_id, asset_type, count in result.all():
            counts[str(project_id)][str(asset_type)] = int(count)
            counts[str(project_id)]["total"] += int(count)
        return counts

    @staticmethod
    def _candidate_statements(*, kb_id: str, uid: str, asset_type: str, query: str | None):
        pattern = f"%{query}%" if query else None
        if asset_type == "paper":
            filters = [AcademicPaper.kb_id == kb_id]
            if pattern:
                filters.append(or_(AcademicPaper.title.ilike(pattern), AcademicPaper.abstract.ilike(pattern)))
            statement = select(AcademicPaper).where(*filters).order_by(AcademicPaper.updated_at.desc())
            count_statement = select(func.count()).select_from(AcademicPaper).where(*filters)
        elif asset_type == "search_run":
            filters = [ResearchSearchRun.kb_id == kb_id, ResearchSearchRun.uid == uid]
            if pattern:
                filters.append(ResearchSearchRun.raw_query.ilike(pattern))
            statement = select(ResearchSearchRun).where(*filters).order_by(ResearchSearchRun.created_at.desc())
            count_statement = select(func.count()).select_from(ResearchSearchRun).where(*filters)
        elif asset_type == "synthesis_run":
            filters = [ResearchSynthesisRun.kb_id == kb_id, ResearchSynthesisRun.uid == uid]
            if pattern:
                filters.append(ResearchSynthesisRun.raw_query.ilike(pattern))
            statement = select(ResearchSynthesisRun).where(*filters).order_by(ResearchSynthesisRun.created_at.desc())
            count_statement = select(func.count()).select_from(ResearchSynthesisRun).where(*filters)
        elif asset_type == "analysis_run":
            filters = [AcademicPaperAnalysisRun.kb_id == kb_id, AcademicPaperAnalysisRun.uid == uid]
            if pattern:
                filters.append(AcademicPaper.title.ilike(pattern))
            statement = (
                select(AcademicPaperAnalysisRun, AcademicPaper)
                .join(AcademicPaper, AcademicPaper.id == AcademicPaperAnalysisRun.academic_paper_id)
                .where(*filters)
                .order_by(AcademicPaperAnalysisRun.created_at.desc())
            )
            count_statement = (
                select(func.count())
                .select_from(AcademicPaperAnalysisRun)
                .join(AcademicPaper, AcademicPaper.id == AcademicPaperAnalysisRun.academic_paper_id)
                .where(*filters)
            )
        elif asset_type == "evaluation_experiment":
            filters = [EvaluationExperiment.source_kb_id == kb_id]
            if pattern:
                filters.append(
                    or_(EvaluationExperiment.name.ilike(pattern), EvaluationExperiment.description.ilike(pattern))
                )
            statement = select(EvaluationExperiment).where(*filters).order_by(EvaluationExperiment.created_at.desc())
            count_statement = select(func.count()).select_from(EvaluationExperiment).where(*filters)
        else:
            raise ValueError(f"Unsupported research project asset type: {asset_type}")
        return statement, count_statement

    @staticmethod
    def _serialize_candidate(asset_type: str, row: Any) -> dict[str, Any]:
        if asset_type == "analysis_run":
            run, paper = row
            return {
                "reference_id": run.run_id,
                "title": f"论文分析 · {paper.title}",
                "summary": f"{run.strategy or 'multi_agent'} · {run.stage or '等待执行'}",
                "status": run.status,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "metadata": {"paper_id": paper.paper_id, "strategy": run.strategy or "multi_agent"},
            }
        record = row[0] if hasattr(row, "__getitem__") else row
        if asset_type == "paper":
            return {
                "reference_id": record.paper_id,
                "title": record.title,
                "summary": record.abstract,
                "status": record.metadata_status,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "metadata": {
                    "authors": record.authors or [],
                    "publication_year": record.publication_year,
                    "venue": record.venue,
                },
            }
        if asset_type == "search_run":
            config = record.retrieval_config or {}
            return {
                "reference_id": record.run_id,
                "title": record.raw_query,
                "summary": record.rewritten_query,
                "status": record.status,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "metadata": {"result_count": record.result_count, "mode": config.get("mode")},
            }
        if asset_type == "synthesis_run":
            return {
                "reference_id": record.run_id,
                "title": record.raw_query,
                "summary": str((record.result or {}).get("summary") or ""),
                "status": record.status,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "metadata": {"stage": record.stage},
            }
        return {
            "reference_id": record.experiment_id,
            "title": record.name,
            "summary": record.description,
            "status": record.status,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "metadata": {
                "completed_variants": record.completed_variants,
                "total_variants": record.total_variants,
                "overall_score": (record.comparison_report or {}).get("overall_score"),
            },
        }


__all__ = ["ASSET_TYPES", "ResearchProjectRepository", "build_project_activity"]
