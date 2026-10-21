"""ResearchCompass 首次使用引导服务。

按真实数据判定“建库 → 上传 → 检索 → 综述”四步是否完成：知识库按当前用户的可访问性
判断，上传、检索与综述按用户自己的记录统计。服务只读不写，不保存引导进度，也不
伪造演示数据；界面只负责展示与跳过。
"""

from __future__ import annotations

from typing import Any

from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.research_onboarding_repository import ResearchOnboardingRepository
from yuxi.storage.postgres.models_business import User

ONBOARDING_STEPS = (
    ("create_knowledge_base", "建库", "新建或加入一个知识库，作为文献与证据的存放位置"),
    ("upload_paper", "上传论文", "在知识库文件管理里上传 PDF，系统会自动完成学术分块与索引"),
    ("run_search", "检索取证", "在科研罗盘执行一次智能检索，得到带原文出处的片段"),
    ("run_synthesis", "生成综述", "基于检索结果生成证据综述，结论必须绑定已验证证据"),
)


def _step_completed(key: str, *, accessible_knowledge_bases: int, progress: dict[str, int]) -> bool:
    completed = {
        "create_knowledge_base": accessible_knowledge_bases > 0,
        "upload_paper": progress["uploaded"] > 0,
        "run_search": progress["searches"] > 0,
        "run_synthesis": progress["syntheses"] > 0,
    }
    return completed[key]


async def get_onboarding_state(*, current_user: User) -> dict[str, Any]:
    """返回四步引导的真实完成状态与下一步建议。"""
    user_info = {
        "uid": str(current_user.uid),
        "role": current_user.role,
        "department_id": current_user.department_id,
    }
    accessible_knowledge_bases = 0
    for record in await KnowledgeBaseRepository().get_all():
        if await knowledge_base.check_accessible(user_info, record.kb_id):
            accessible_knowledge_bases += 1

    progress = await ResearchOnboardingRepository().count_user_progress(uid=str(current_user.uid))
    steps = [
        {
            "key": key,
            "title": title,
            "hint": hint,
            "completed": _step_completed(key, accessible_knowledge_bases=accessible_knowledge_bases, progress=progress),
        }
        for key, title, hint in ONBOARDING_STEPS
    ]
    completed_count = sum(1 for step in steps if step["completed"])
    return {
        "steps": steps,
        "completed_count": completed_count,
        "all_completed": completed_count == len(steps),
        "next_step": next((step["key"] for step in steps if not step["completed"]), None),
        "knowledge_base_count": accessible_knowledge_bases,
        "uploaded_paper_count": progress["uploaded"],
        "search_run_count": progress["searches"],
        "synthesis_run_count": progress["syntheses"],
    }


__all__ = ["ONBOARDING_STEPS", "get_onboarding_state"]
