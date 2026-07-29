"""ResearchCompass 论文分析运行数据访问层。

本模块是本仓库在开源智能体框架 Yuxi 的持久化基础设施之上实现的论文分析运行仓储，
封装单篇论文分析运行记录的创建、状态推进与序列化；通用 PostgreSQL 连接池与 ORM 模型
基类由 Yuxi 提供，本模块只负责分析运行业务的读写逻辑。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import AcademicPaperAnalysisRun


class AcademicPaperAnalysisRepository:
    async def create(
        self,
        *,
        run_id: str,
        kb_id: str,
        academic_paper_id: int,
        uid: str,
        model_config: dict[str, Any],
        strategy: str = "multi_agent",
    ) -> AcademicPaperAnalysisRun:
        record = AcademicPaperAnalysisRun(
            run_id=run_id,
            kb_id=kb_id,
            academic_paper_id=academic_paper_id,
            uid=uid,
            model_config_json=model_config,
            strategy=strategy,
            status="pending",
            stage_results={},
        )
        async with pg_manager.get_async_session_context() as session:
            session.add(record)
            await session.flush()
        return record

    async def get(self, run_id: str) -> AcademicPaperAnalysisRun | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(AcademicPaperAnalysisRun).where(AcademicPaperAnalysisRun.run_id == run_id)
            )

    async def get_latest(
        self,
        *,
        kb_id: str,
        academic_paper_id: int,
        uid: str,
        strategy: str | None = "multi_agent",
    ) -> AcademicPaperAnalysisRun | None:
        async with pg_manager.get_async_session_context() as session:
            filters = [
                AcademicPaperAnalysisRun.kb_id == kb_id,
                AcademicPaperAnalysisRun.academic_paper_id == academic_paper_id,
                AcademicPaperAnalysisRun.uid == uid,
            ]
            if strategy:
                filters.append(AcademicPaperAnalysisRun.strategy == strategy)
            return await session.scalar(
                select(AcademicPaperAnalysisRun)
                .where(*filters)
                .order_by(AcademicPaperAnalysisRun.created_at.desc(), AcademicPaperAnalysisRun.id.desc())
                .limit(1)
            )

    async def list_recoverable(self) -> list[AcademicPaperAnalysisRun]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(AcademicPaperAnalysisRun).where(AcademicPaperAnalysisRun.status.in_(["pending", "running"]))
            )
            return list(result.scalars().all())

    async def update(self, run_id: str, values: dict[str, Any]) -> AcademicPaperAnalysisRun | None:
        allowed = {
            "status",
            "strategy",
            "stage",
            "stage_results",
            "result",
            "error_type",
            "error_message",
            "started_at",
            "completed_at",
        }
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(AcademicPaperAnalysisRun).where(AcademicPaperAnalysisRun.run_id == run_id)
            )
            if record is None:
                return None
            for key, value in values.items():
                if key in allowed:
                    setattr(record, key, value)
            await session.flush()
            return record

    @staticmethod
    def serialize(record: AcademicPaperAnalysisRun) -> dict[str, Any]:
        return {
            "run_id": record.run_id,
            "kb_id": record.kb_id,
            "academic_paper_id": record.academic_paper_id,
            "status": record.status,
            "stage": record.stage,
            "stage_results": record.stage_results or {},
            "result": record.result,
            "model_config": record.model_config_json or {},
            "strategy": record.strategy or "multi_agent",
            "error_type": record.error_type,
            "error_message": record.error_message,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }


__all__ = ["AcademicPaperAnalysisRepository"]
