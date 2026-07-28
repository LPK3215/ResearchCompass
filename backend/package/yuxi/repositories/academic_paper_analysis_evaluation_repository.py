"""ResearchCompass 论文分析评估数据访问层。

本模块是本仓库在开源智能体框架 Yuxi 的持久化基础设施之上实现的论文分析评估仓储，
封装评估批次、评估条目、盲评打分的创建与查询；通用 PostgreSQL 连接池与 ORM 模型基类
由 Yuxi 提供，本模块只负责评估业务的读写逻辑。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    AcademicPaperAnalysisEvaluation,
    AcademicPaperAnalysisEvaluationItem,
    AcademicPaperAnalysisEvaluationScore,
    AcademicPaperAnalysisRun,
    AcademicPaper,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AcademicPaperAnalysisEvaluationRepository:
    async def create_with_items(
        self, evaluation_data: dict[str, Any], items_data: list[dict[str, Any]]
    ) -> AcademicPaperAnalysisEvaluation:
        evaluation = AcademicPaperAnalysisEvaluation(**evaluation_data)
        items = [AcademicPaperAnalysisEvaluationItem(**item) for item in items_data]
        async with pg_manager.get_async_session_context() as session:
            session.add(evaluation)
            session.add_all(items)
        return evaluation

    async def get(self, evaluation_id: str) -> AcademicPaperAnalysisEvaluation | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(AcademicPaperAnalysisEvaluation).where(
                    AcademicPaperAnalysisEvaluation.evaluation_id == evaluation_id
                )
            )

    async def list_by_kb_id(self, kb_id: str) -> list[AcademicPaperAnalysisEvaluation]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaperAnalysisEvaluation)
                .where(AcademicPaperAnalysisEvaluation.kb_id == kb_id)
                .order_by(AcademicPaperAnalysisEvaluation.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_recoverable(self) -> list[AcademicPaperAnalysisEvaluation]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaperAnalysisEvaluation).where(
                    AcademicPaperAnalysisEvaluation.status.in_(["queued", "running"])
                )
            )
            return list(result.scalars().all())

    async def update_evaluation(
        self, evaluation_id: str, values: dict[str, Any]
    ) -> AcademicPaperAnalysisEvaluation | None:
        allowed = {
            "status",
            "completed_pairs",
            "task_id",
            "error_message",
            "started_at",
            "completed_at",
        }
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(AcademicPaperAnalysisEvaluation).where(
                    AcademicPaperAnalysisEvaluation.evaluation_id == evaluation_id
                )
            )
            if record is None:
                return None
            for key, value in values.items():
                if key in allowed:
                    setattr(record, key, value)
            return record

    async def list_items(self, evaluation_id: str) -> list[AcademicPaperAnalysisEvaluationItem]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaperAnalysisEvaluationItem)
                .where(AcademicPaperAnalysisEvaluationItem.evaluation_id == evaluation_id)
                .order_by(AcademicPaperAnalysisEvaluationItem.item_index.asc())
            )
            return list(result.scalars().all())

    async def get_item(self, item_id: str) -> AcademicPaperAnalysisEvaluationItem | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(AcademicPaperAnalysisEvaluationItem).where(
                    AcademicPaperAnalysisEvaluationItem.item_id == item_id
                )
            )

    async def update_item(self, item_id: str, values: dict[str, Any]) -> AcademicPaperAnalysisEvaluationItem | None:
        allowed = {"single_run_id", "multi_run_id", "status", "error_message", "completed_at"}
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(AcademicPaperAnalysisEvaluationItem).where(
                    AcademicPaperAnalysisEvaluationItem.item_id == item_id
                )
            )
            if record is None:
                return None
            for key, value in values.items():
                if key in allowed:
                    setattr(record, key, value)
            return record

    async def get_paper(self, paper_id: int) -> AcademicPaper | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(select(AcademicPaper).where(AcademicPaper.id == paper_id))

    async def get_run(self, run_id: str) -> AcademicPaperAnalysisRun | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(AcademicPaperAnalysisRun).where(AcademicPaperAnalysisRun.run_id == run_id)
            )

    async def upsert_score(
        self, *, evaluation_id: str, item_id: str, scorer_uid: str, blind_scores: dict[str, Any], notes: str
    ) -> AcademicPaperAnalysisEvaluationScore:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(AcademicPaperAnalysisEvaluationScore).where(
                    AcademicPaperAnalysisEvaluationScore.item_id == item_id,
                    AcademicPaperAnalysisEvaluationScore.scorer_uid == scorer_uid,
                )
            )
            if record is None:
                record = AcademicPaperAnalysisEvaluationScore(
                    score_id=f"analysis_score_{uuid.uuid4().hex[:12]}",
                    evaluation_id=evaluation_id,
                    item_id=item_id,
                    scorer_uid=scorer_uid,
                    blind_scores=blind_scores,
                    notes=notes or None,
                )
                session.add(record)
            else:
                record.blind_scores = blind_scores
                record.notes = notes or None
                record.submitted_at = _now()
            return record

    async def list_scores(self, evaluation_id: str) -> list[AcademicPaperAnalysisEvaluationScore]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaperAnalysisEvaluationScore)
                .where(AcademicPaperAnalysisEvaluationScore.evaluation_id == evaluation_id)
                .order_by(AcademicPaperAnalysisEvaluationScore.submitted_at.asc())
            )
            return list(result.scalars().all())

    async def count_scores(self, evaluation_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(AcademicPaperAnalysisEvaluationScore)
                    .where(AcademicPaperAnalysisEvaluationScore.evaluation_id == evaluation_id)
                )
                or 0
            )


__all__ = ["AcademicPaperAnalysisEvaluationRepository"]
