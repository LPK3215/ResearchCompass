"""ResearchCompass 研究综述数据访问层。

本模块是本仓库在开源智能体框架 Yuxi 的持久化基础设施之上实现的研究综述仓储，
封装综述运行记录的创建、状态推进、取消语义、可恢复任务列举与序列化；
通用 PostgreSQL 连接池与 ORM 模型基类由 Yuxi 提供，本模块只负责综述业务的读写逻辑。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import ResearchSynthesisRun


TERMINAL_SYNTHESIS_STATUSES = {"success", "failed", "cancelled"}


class ResearchSynthesisRepository:
    async def create(
        self,
        *,
        run_id: str,
        kb_id: str,
        uid: str,
        raw_query: str,
        model_config: dict[str, Any],
        retrieval_config: dict[str, Any],
        parent_run_id: str | None = None,
    ) -> ResearchSynthesisRun:
        record = ResearchSynthesisRun(
            run_id=run_id,
            parent_run_id=parent_run_id,
            kb_id=kb_id,
            uid=uid,
            raw_query=raw_query,
            model_config_json=model_config,
            retrieval_config=retrieval_config,
            active_key="active",
            status="pending",
            stage="pending",
            stage_timings={},
        )
        async with pg_manager.get_async_session_context() as session:
            session.add(record)
            await session.flush()
        return record

    async def get(self, run_id: str) -> ResearchSynthesisRun | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(select(ResearchSynthesisRun).where(ResearchSynthesisRun.run_id == run_id))

    async def get_active(self, *, kb_id: str, uid: str) -> ResearchSynthesisRun | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(ResearchSynthesisRun)
                .where(
                    ResearchSynthesisRun.kb_id == kb_id,
                    ResearchSynthesisRun.uid == uid,
                    ResearchSynthesisRun.active_key == "active",
                )
                .order_by(ResearchSynthesisRun.created_at.desc(), ResearchSynthesisRun.id.desc())
                .limit(1)
            )

    async def list_for_user(
        self,
        *,
        kb_id: str,
        uid: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResearchSynthesisRun], int]:
        filters = [ResearchSynthesisRun.kb_id == kb_id, ResearchSynthesisRun.uid == uid]
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count()).select_from(ResearchSynthesisRun).where(*filters))
            result = await session.execute(
                select(ResearchSynthesisRun)
                .where(*filters)
                .order_by(ResearchSynthesisRun.created_at.desc(), ResearchSynthesisRun.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all()), int(total or 0)

    async def list_recoverable(self) -> list[ResearchSynthesisRun]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchSynthesisRun).where(
                    ResearchSynthesisRun.status.in_(["pending", "retrieving", "synthesizing", "validating"])
                )
            )
            return list(result.scalars().all())

    async def update(self, run_id: str, values: dict[str, Any]) -> ResearchSynthesisRun | None:
        updates = self._prepare_updates(values)

        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(select(ResearchSynthesisRun).where(ResearchSynthesisRun.run_id == run_id))
            if record is None:
                return None
            for key, value in updates.items():
                setattr(record, key, value)
            await session.flush()
            return record

    async def update_if_not_cancelled(self, run_id: str, values: dict[str, Any]) -> ResearchSynthesisRun | None:
        """持久化 worker 的状态推进；若用户已取消则跳过本次写入。"""
        updates = self._prepare_updates(values)
        if not updates:
            return await self.get(run_id)
        async with pg_manager.get_async_session_context() as session:
            statement = (
                update(ResearchSynthesisRun)
                .where(
                    ResearchSynthesisRun.run_id == run_id,
                    ResearchSynthesisRun.status != "cancelled",
                )
                .values(**updates)
                .returning(ResearchSynthesisRun)
            )
            result = await session.execute(statement)
            return result.scalar_one_or_none()

    async def mark_cancelled(self, run_id: str, *, completed_at: Any) -> ResearchSynthesisRun | None:
        """将综述运行置为终态取消，但不会撤销已成功完成的结果。"""
        async with pg_manager.get_async_session_context() as session:
            statement = (
                update(ResearchSynthesisRun)
                .where(
                    ResearchSynthesisRun.run_id == run_id,
                    ResearchSynthesisRun.status.not_in(TERMINAL_SYNTHESIS_STATUSES),
                )
                .values(
                    status="cancelled",
                    stage="cancelled",
                    error_type="synthesis_cancelled",
                    error_message="用户已取消研究综述",
                    completed_at=completed_at,
                    active_key=None,
                )
                .returning(ResearchSynthesisRun)
            )
            result = await session.execute(statement)
            return result.scalar_one_or_none()

    @staticmethod
    def _prepare_updates(values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "task_id",
            "status",
            "stage",
            "retrieval_snapshot",
            "result",
            "stage_timings",
            "error_type",
            "error_message",
            "started_at",
            "completed_at",
            "active_key",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if updates.get("status") in TERMINAL_SYNTHESIS_STATUSES:
            updates["active_key"] = None
        return updates

    @staticmethod
    def serialize(record: ResearchSynthesisRun, *, include_snapshot: bool = False) -> dict[str, Any]:
        payload = {
            "run_id": record.run_id,
            "parent_run_id": record.parent_run_id,
            "kb_id": record.kb_id,
            "task_id": record.task_id,
            "query": record.raw_query,
            "status": record.status,
            "stage": record.stage,
            "model_config": record.model_config_json or {},
            "retrieval_config": record.retrieval_config or {},
            "result": record.result,
            "stage_timings": record.stage_timings or {},
            "error_type": record.error_type,
            "error_message": record.error_message,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }
        if include_snapshot:
            payload["retrieval_snapshot"] = record.retrieval_snapshot
        return payload


__all__ = ["ResearchSynthesisRepository", "TERMINAL_SYNTHESIS_STATUSES"]
