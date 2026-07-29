"""ResearchCompass 研究检索运行数据访问层。

本模块是本仓库在开源智能体框架 Yuxi 的持久化基础设施之上实现的研究检索运行仓储，
封装检索运行记录的创建、状态推进、结果快照写入与序列化；通用 PostgreSQL 连接池与
ORM 模型基类由 Yuxi 提供，本模块只负责检索运行业务的读写逻辑。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import load_only

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import ResearchSearchRun


class ResearchSearchRunRepository:
    async def create(
        self,
        *,
        run_id: str,
        kb_id: str,
        uid: str,
        raw_query: str,
        model_config: dict[str, Any],
        retrieval_config: dict[str, Any],
    ) -> ResearchSearchRun:
        record = ResearchSearchRun(
            run_id=run_id,
            kb_id=kb_id,
            uid=uid,
            raw_query=raw_query,
            model_config_json=model_config,
            retrieval_config=retrieval_config,
            status="running",
            stage_timings={},
            result_count=0,
            started_at=datetime.now(UTC).replace(tzinfo=None),
        )
        async with pg_manager.get_async_session_context() as session:
            session.add(record)
            await session.flush()
        return record

    async def update(self, run_id: str, values: dict[str, Any]) -> ResearchSearchRun | None:
        allowed = {
            "model_config_json",
            "rewritten_query",
            "rewrite_keywords",
            "status",
            "stage_timings",
            "result_count",
            "result_snapshot",
            "is_pinned",
            "error_type",
            "error_message",
            "completed_at",
        }
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(ResearchSearchRun).where(ResearchSearchRun.run_id == run_id))
            record = result.scalar_one_or_none()
            if record is None:
                return None
            for key, value in values.items():
                if key in allowed:
                    setattr(record, key, value)
            await session.flush()
            return record

    async def get(self, run_id: str, *, uid: str | None = None) -> ResearchSearchRun | None:
        filters = [ResearchSearchRun.run_id == run_id]
        if uid is not None:
            filters.append(ResearchSearchRun.uid == uid)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(ResearchSearchRun).where(*filters))
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        *,
        kb_id: str,
        uid: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResearchSearchRun], int]:
        filters = [ResearchSearchRun.kb_id == kb_id, ResearchSearchRun.uid == uid]
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count()).select_from(ResearchSearchRun).where(*filters))
            result = await session.execute(
                select(ResearchSearchRun)
                .options(
                    load_only(
                        ResearchSearchRun.run_id,
                        ResearchSearchRun.kb_id,
                        ResearchSearchRun.raw_query,
                        ResearchSearchRun.rewritten_query,
                        ResearchSearchRun.rewrite_keywords,
                        ResearchSearchRun.model_config_json,
                        ResearchSearchRun.retrieval_config,
                        ResearchSearchRun.status,
                        ResearchSearchRun.stage_timings,
                        ResearchSearchRun.result_count,
                        ResearchSearchRun.is_pinned,
                        ResearchSearchRun.error_type,
                        ResearchSearchRun.error_message,
                        ResearchSearchRun.created_at,
                        ResearchSearchRun.started_at,
                        ResearchSearchRun.completed_at,
                    )
                )
                .where(*filters)
                .order_by(
                    ResearchSearchRun.is_pinned.desc(),
                    ResearchSearchRun.created_at.desc(),
                    ResearchSearchRun.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all()), int(total or 0)

    async def delete(self, run_id: str, *, uid: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(ResearchSearchRun).where(
                    ResearchSearchRun.run_id == run_id,
                    ResearchSearchRun.uid == uid,
                )
            )
            return result.rowcount > 0

    @staticmethod
    def serialize(record: ResearchSearchRun, *, include_result: bool = False) -> dict[str, Any]:
        payload = {
            "run_id": record.run_id,
            "kb_id": record.kb_id,
            "query": record.raw_query,
            "rewritten_query": record.rewritten_query,
            "keywords": record.rewrite_keywords or [],
            "status": record.status,
            "config": {
                "models": record.model_config_json or {},
                "retrieval": record.retrieval_config or {},
            },
            "stage_timings": record.stage_timings or {},
            "result_count": record.result_count,
            "is_pinned": bool(record.is_pinned),
            "error_type": record.error_type,
            "error_message": record.error_message,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }
        if include_result:
            payload["result"] = record.result_snapshot
        return payload


__all__ = ["ResearchSearchRunRepository"]
