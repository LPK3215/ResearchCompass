from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

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


__all__ = ["ResearchSearchRunRepository"]
