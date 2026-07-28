"""ResearchCompass 研究助手会话编排服务。

本模块是本仓库作者在开源智能体框架 Yuxi 之上设计的科研协作入口，负责把"知识库 +
研究项目 + 当前选中对象"组装成一份研究会话上下文，并将研究助手 Agent 绑定到该上下文，
确保同一研究作用域下复用同一条会话。会话存储、Agent 注册与运行调度等通用智能体运行时
能力由 Yuxi 提供，本模块仅决定科研上下文的语义与作用域归属。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_repository import AgentRepository, RESEARCH_COPILOT_AGENT_SLUG
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.services.research_project_service import ResearchProjectError, get_owned_project
from yuxi.storage.postgres.models_business import User

RESEARCH_COPILOT_SOURCE = "research_copilot"


class ResearchCopilotError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


class ResearchSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "paper",
        "search_run",
        "synthesis_run",
        "analysis_run",
        "milestone",
        "task",
        "project_asset",
    ]
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=500)


class ResearchCopilotScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kb_id: str = Field(min_length=1, max_length=64)
    project_id: str | None = Field(default=None, max_length=64)
    surface: Literal[
        "projects",
        "library",
        "search",
        "synthesis",
        "graph",
        "trends",
        "opportunities",
        "analysis-evaluations",
        "user-studies",
    ] = "projects"
    selection: ResearchSelection | None = None


def _thread_payload(conversation) -> dict[str, Any]:
    return {
        "id": conversation.thread_id,
        "uid": conversation.uid,
        "agent_id": conversation.agent_id,
        "title": conversation.title,
        "is_pinned": bool(conversation.is_pinned),
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "metadata": conversation.extra_metadata or {},
    }


async def validate_research_context(payload: dict[str, Any], *, current_user: User) -> dict[str, Any]:
    try:
        scope = ResearchCopilotScope.model_validate(payload)
    except ValueError as exc:
        raise ResearchCopilotError("invalid_research_context", "研究上下文格式无效") from exc

    try:
        await _ensure_access(current_user, scope.kb_id)
    except HTTPException as exc:
        error_type = "forbidden" if exc.status_code == 403 else "knowledge_base_not_found"
        raise ResearchCopilotError(error_type, str(exc.detail)) from exc

    knowledge_base = await KnowledgeBaseRepository().get_by_kb_id(scope.kb_id)
    if knowledge_base is None:
        raise ResearchCopilotError("knowledge_base_not_found", "知识库不存在")

    project_title = ""
    if scope.project_id:
        try:
            project = await get_owned_project(scope.project_id, current_user)
        except ResearchProjectError as exc:
            raise ResearchCopilotError(exc.error_type, exc.message) from exc
        if str(project.kb_id) != scope.kb_id:
            raise ResearchCopilotError("project_scope_mismatch", "研究项目不属于当前知识库")
        project_title = str(project.title)

    context = scope.model_dump(mode="json")
    context.update(
        {
            "kb_name": str(knowledge_base.name or scope.kb_id),
            "project_title": project_title,
            "scope_key": f"{scope.kb_id}:{scope.project_id or 'library'}",
        }
    )
    return context


async def ensure_research_copilot_thread(
    *,
    payload: dict[str, Any],
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    context = await validate_research_context(payload, current_user=current_user)
    agent = await AgentRepository(db).ensure_research_copilot_agent()
    repository = ConversationRepository(db)
    scope_identity = {
        "uid": str(current_user.uid),
        "agent_id": agent.slug,
        "source": RESEARCH_COPILOT_SOURCE,
        "scope_key": context["scope_key"],
    }
    await repository.lock_source_scope(**scope_identity)
    conversation = await repository.get_active_by_source_scope(
        **scope_identity,
    )
    metadata = {
        "source": RESEARCH_COPILOT_SOURCE,
        "research_scope_key": context["scope_key"],
        "research_context": context,
    }
    if conversation is None:
        title = context.get("project_title") or f"{context['kb_name']} 研究助手"
        conversation = await repository.create_conversation(
            uid=str(current_user.uid),
            agent_id=agent.slug,
            title=title,
            metadata=metadata,
        )
    else:
        conversation = await repository.update_conversation(conversation.thread_id, metadata=metadata)
        if conversation is None:
            raise ResearchCopilotError("thread_update_failed", "研究助手会话更新失败")

    return {"agent_id": agent.slug, "thread": _thread_payload(conversation), "research_context": context}


async def prepare_research_copilot_run_meta(
    *,
    agent_slug: str,
    thread_id: str,
    meta: dict[str, Any],
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    if agent_slug != RESEARCH_COPILOT_AGENT_SLUG:
        cleaned_meta = dict(meta)
        cleaned_meta.pop("research_context", None)
        return cleaned_meta

    conversation = await ConversationRepository(db).get_conversation_by_thread_id(thread_id)
    if (
        conversation is None
        or conversation.uid != str(current_user.uid)
        or conversation.agent_id != RESEARCH_COPILOT_AGENT_SLUG
    ):
        raise ResearchCopilotError("thread_not_found", "研究助手会话不存在")

    metadata = conversation.extra_metadata or {}
    if metadata.get("source") != RESEARCH_COPILOT_SOURCE:
        raise ResearchCopilotError("invalid_copilot_thread", "当前会话不是 ResearchCompass 研究会话")
    persisted_context = metadata.get("research_context") or {}
    scope = {
        field: persisted_context[field] for field in ResearchCopilotScope.model_fields if field in persisted_context
    }
    context = await validate_research_context(scope, current_user=current_user)
    return {**meta, "source": RESEARCH_COPILOT_SOURCE, "research_context": context}


__all__ = [
    "RESEARCH_COPILOT_SOURCE",
    "ResearchCopilotError",
    "ResearchCopilotScope",
    "ensure_research_copilot_thread",
    "prepare_research_copilot_run_meta",
    "validate_research_context",
]
