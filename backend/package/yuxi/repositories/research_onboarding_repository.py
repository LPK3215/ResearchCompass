"""ResearchCompass 新手引导数据仓储。

只读统计当前用户的真实使用痕迹（上传文件、成功检索、成功综述），供首次使用引导
判断步骤完成状态；不写入任何引导进度，避免出现演示数据。
"""

from __future__ import annotations

from sqlalchemy import func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeFile, ResearchSearchRun, ResearchSynthesisRun


class ResearchOnboardingRepository:
    async def count_user_progress(self, *, uid: str) -> dict[str, int]:
        """统计用户上传的非目录文件、成功检索与成功综述数量。"""
        async with pg_manager.get_async_session_context() as session:
            uploaded = await session.scalar(
                select(func.count())
                .select_from(KnowledgeFile)
                .where(KnowledgeFile.created_by == uid, KnowledgeFile.is_folder.is_(False))
            )
            searches = await session.scalar(
                select(func.count())
                .select_from(ResearchSearchRun)
                .where(ResearchSearchRun.uid == uid, ResearchSearchRun.status == "success")
            )
            syntheses = await session.scalar(
                select(func.count())
                .select_from(ResearchSynthesisRun)
                .where(ResearchSynthesisRun.uid == uid, ResearchSynthesisRun.status == "success")
            )
        return {"uploaded": int(uploaded or 0), "searches": int(searches or 0), "syntheses": int(syntheses or 0)}
