from collections.abc import Awaitable
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException
from langchain_core.tools import ToolException
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field, model_validator

from yuxi.agents.toolkits.registry import tool
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.manager import pg_manager

AssetType = Literal["paper", "search_run", "synthesis_run", "analysis_run", "evaluation_experiment"]


class EmptyInput(BaseModel):
    dummy: str = Field(default="", description="无需填写，忽略此参数")


class ProjectReferenceInput(BaseModel):
    project_id: str | None = Field(
        default=None,
        max_length=64,
        description="研究项目 ID；当前会话已绑定项目时应省略",
    )


class ListProjectsInput(BaseModel):
    status: Literal["active", "completed", "archived"] | None = None
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)


class CreateProjectInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    research_question: str = Field(min_length=1, max_length=8000)
    description: str = Field(default="", max_length=12000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    target_date: date | None = None
    next_action: str = Field(default="", max_length=4000)


class ProjectUpdates(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    research_question: str | None = Field(default=None, min_length=1, max_length=8000)
    description: str | None = Field(default=None, max_length=12000)
    progress: int | None = Field(default=None, ge=0, le=100)
    next_action: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=20)
    target_date: date | None = None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个项目更新字段")
        return self


class UpdateProjectInput(ProjectReferenceInput):
    changes: ProjectUpdates


class SetProjectStatusInput(ProjectReferenceInput):
    status: Literal["active", "completed", "archived"]


class MilestoneChanges(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=12000)
    status: Literal["planned", "active", "completed"] | None = None
    target_date: date | None = None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个里程碑更新字段")
        return self


class CreateMilestoneInput(ProjectReferenceInput):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=12000)
    status: Literal["planned", "active", "completed"] = "planned"
    target_date: date | None = None


class UpdateMilestoneInput(ProjectReferenceInput):
    milestone_id: str = Field(min_length=1, max_length=64)
    changes: MilestoneChanges


class DeleteMilestoneInput(ProjectReferenceInput):
    milestone_id: str = Field(min_length=1, max_length=64)


class TaskChanges(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=12000)
    status: Literal["todo", "in_progress", "blocked", "done"] | None = None
    priority: Literal["low", "medium", "high"] | None = None
    due_date: date | None = None
    milestone_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个任务更新字段")
        return self


class CreateTaskInput(ProjectReferenceInput):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=12000)
    status: Literal["todo", "in_progress", "blocked", "done"] = "todo"
    priority: Literal["low", "medium", "high"] = "medium"
    due_date: date | None = None
    milestone_id: str | None = Field(default=None, max_length=64)


class UpdateTaskInput(ProjectReferenceInput):
    task_id: str = Field(min_length=1, max_length=64)
    changes: TaskChanges


class DeleteTaskInput(ProjectReferenceInput):
    task_id: str = Field(min_length=1, max_length=64)


class SearchPapersInput(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    retrieval_mode: Literal["local_hybrid", "strict_hybrid_citation_graph"] = "local_hybrid"
    top_k: int = Field(default=10, ge=1, le=50)
    recall_top_k: int = Field(default=50, ge=10, le=200)
    year_from: int | None = Field(default=None, ge=1500)
    year_to: int | None = Field(default=None, ge=1500)


class RunReferenceInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class ExportSynthesisInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    format: Literal["markdown", "docx"] = Field(default="markdown", description="导出格式")


class CreateSynthesisInput(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=8, ge=2, le=20)
    recall_top_k: int = Field(default=50, ge=2, le=200)
    year_from: int | None = Field(default=None, ge=1500)
    year_to: int | None = Field(default=None, ge=1500)


class ListAssetCandidatesInput(ProjectReferenceInput):
    asset_type: AssetType
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)


class AddProjectAssetsInput(ProjectReferenceInput):
    asset_type: AssetType
    reference_ids: list[str] = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=4000)


class ListProjectAssetsInput(ProjectReferenceInput):
    asset_type: AssetType | None = None
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=50, ge=1, le=100)


class ProjectAssetInput(ProjectReferenceInput):
    asset_id: str = Field(min_length=1, max_length=64)


class LinkAssetInput(ProjectAssetInput):
    milestone_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_target(self):
        if bool(self.milestone_id) == bool(self.task_id):
            raise ValueError("必须且只能指定一个里程碑或任务")
        return self


class UnlinkAssetInput(ProjectReferenceInput):
    link_id: str = Field(min_length=1, max_length=64)


def _research_context(runtime: ToolRuntime) -> dict[str, Any]:
    context = getattr(runtime, "context", None)
    research_context = getattr(context, "research_context", None)
    if not isinstance(research_context, dict) or not research_context.get("kb_id"):
        raise ToolException("当前会话没有有效的 ResearchCompass 研究上下文")
    return research_context


def _ensure_context_kb(runtime: ToolRuntime, actual_kb_id: object, resource_name: str) -> None:
    context = _research_context(runtime)
    if str(actual_kb_id or "") != str(context["kb_id"]):
        raise ToolException(f"{resource_name}不属于当前知识库，请先切换知识库")


async def _project_id(runtime: ToolRuntime, requested: str | None, current_user: Any) -> str:
    from yuxi.services.research_project_service import get_owned_project

    context = _research_context(runtime)
    current_project_id = str(context.get("project_id") or "").strip()
    requested_project_id = str(requested or "").strip()
    if current_project_id and requested_project_id and current_project_id != requested_project_id:
        raise ToolException("不能在当前项目会话中修改另一个研究项目，请先切换项目")
    project_id = requested_project_id or current_project_id
    if not project_id:
        raise ToolException("当前会话未绑定研究项目，请提供 project_id 或先在工作台选择项目")
    project = await _call(get_owned_project(project_id, current_user))
    _ensure_context_kb(runtime, project.kb_id, "研究项目")
    return project_id


async def _current_user(runtime: ToolRuntime):
    uid = str(getattr(getattr(runtime, "context", None), "uid", "") or "").strip()
    if not uid:
        raise ToolException("当前 Agent 运行缺少用户身份")
    async with pg_manager.get_async_session_context() as db:
        user = await UserRepository().get_by_uid_with_db(db, uid)
    if user is None:
        raise ToolException("当前用户不存在")
    return user


async def _call(operation: Awaitable[Any]) -> Any:
    from yuxi.services.research_project_service import ResearchProjectError
    from yuxi.services.research_search_service import ResearchSearchError
    from yuxi.services.research_synthesis_service import ResearchSynthesisError

    try:
        return await operation
    except (ResearchProjectError, ResearchSearchError, ResearchSynthesisError) as exc:
        raise ToolException(exc.message) from exc
    except HTTPException as exc:
        detail = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
        raise ToolException(str(detail or "ResearchCompass 操作失败")) from exc


@tool(category="research", tags=["科研", "上下文"], display_name="读取研究上下文", args_schema=EmptyInput)
async def research_get_context(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """读取当前 ResearchCompass 知识库、项目、工作区和选中对象上下文。"""
    del dummy
    return _research_context(runtime)


@tool(category="research", tags=["科研", "项目"], display_name="查询研究项目", args_schema=ListProjectsInput)
async def research_list_projects(
    status: str | None,
    query: str | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """查询当前知识库中属于当前用户的研究项目。"""
    from yuxi.services.research_project_service import list_research_projects

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        list_research_projects(
            kb_id=context["kb_id"],
            current_user=user,
            status=status,
            query=query.strip() if query else None,
            offset=0,
            limit=limit,
        )
    )


@tool(category="research", tags=["科研", "项目"], display_name="读取项目全貌", args_schema=ProjectReferenceInput)
async def research_get_project(project_id: str | None, runtime: ToolRuntime) -> dict[str, Any]:
    """读取研究项目状态、健康度、成果统计和近期活动。"""
    from yuxi.services.research_project_service import get_research_project

    user = await _current_user(runtime)
    return await _call(
        get_research_project(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
        )
    )


@tool(category="research", tags=["科研", "项目"], display_name="创建研究项目", args_schema=CreateProjectInput)
async def research_create_project(
    title: str,
    research_question: str,
    description: str,
    tags: list[str],
    target_date: date | None,
    next_action: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """在当前知识库创建可管理的研究项目。"""
    from yuxi.services.research_project_service import create_research_project

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        create_research_project(
            kb_id=context["kb_id"],
            current_user=user,
            title=title.strip(),
            research_question=research_question.strip(),
            description=description.strip(),
            tags=tags,
            target_date=target_date,
            next_action=next_action.strip(),
        )
    )


@tool(category="research", tags=["科研", "项目"], display_name="更新研究项目", args_schema=UpdateProjectInput)
async def research_update_project(
    project_id: str | None,
    changes: ProjectUpdates,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """更新当前研究项目的标题、问题、说明、进度、下一步、标签或目标日期，不改变项目状态。"""
    from yuxi.services.research_project_service import update_research_project

    user = await _current_user(runtime)
    return await _call(
        update_research_project(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            values=changes.model_dump(exclude_unset=True),
        )
    )


@tool(
    category="research", tags=["科研", "项目", "敏感"], display_name="变更项目状态", args_schema=SetProjectStatusInput
)
async def research_set_project_status(
    project_id: str | None,
    status: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """完成、归档或恢复研究项目。此操作需要用户审批。"""
    from yuxi.services.research_project_service import update_research_project

    user = await _current_user(runtime)
    return await _call(
        update_research_project(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            values={"status": status},
        )
    )


@tool(
    category="research", tags=["科研", "项目", "敏感"], display_name="删除研究项目", args_schema=ProjectReferenceInput
)
async def research_delete_project(project_id: str | None, runtime: ToolRuntime) -> dict[str, Any]:
    """永久删除当前研究项目及其计划关联。此操作需要用户审批。"""
    from yuxi.services.research_project_service import delete_research_project

    user = await _current_user(runtime)
    resolved = await _project_id(runtime, project_id, user)
    await _call(delete_research_project(project_id=resolved, current_user=user))
    return {"deleted": True, "project_id": resolved}


@tool(category="research", tags=["科研", "计划"], display_name="读取项目计划", args_schema=ProjectReferenceInput)
async def research_get_project_plan(project_id: str | None, runtime: ToolRuntime) -> dict[str, Any]:
    """读取当前项目的里程碑、任务、截止日期、成果关联和执行摘要。"""
    from yuxi.services.research_project_plan_service import get_research_project_plan

    user = await _current_user(runtime)
    return await _call(
        get_research_project_plan(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
        )
    )


@tool(category="research", tags=["科研", "计划"], display_name="创建里程碑", args_schema=CreateMilestoneInput)
async def research_create_milestone(
    project_id: str | None,
    title: str,
    description: str,
    status: str,
    target_date: date | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """在当前项目中创建里程碑。"""
    from yuxi.services.research_project_plan_service import create_project_milestone

    user = await _current_user(runtime)
    return await _call(
        create_project_milestone(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            title=title.strip(),
            description=description.strip(),
            status=status,
            target_date=target_date,
        )
    )


@tool(category="research", tags=["科研", "计划"], display_name="更新里程碑", args_schema=UpdateMilestoneInput)
async def research_update_milestone(
    project_id: str | None,
    milestone_id: str,
    changes: MilestoneChanges,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """更新当前项目的里程碑内容或状态。"""
    from yuxi.services.research_project_plan_service import update_project_milestone

    user = await _current_user(runtime)
    return await _call(
        update_project_milestone(
            project_id=await _project_id(runtime, project_id, user),
            milestone_id=milestone_id,
            current_user=user,
            values=changes.model_dump(exclude_unset=True),
        )
    )


@tool(category="research", tags=["科研", "计划", "敏感"], display_name="删除里程碑", args_schema=DeleteMilestoneInput)
async def research_delete_milestone(
    project_id: str | None,
    milestone_id: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """删除当前项目的里程碑。此操作需要用户审批。"""
    from yuxi.services.research_project_plan_service import delete_project_milestone

    user = await _current_user(runtime)
    resolved = await _project_id(runtime, project_id, user)
    await _call(delete_project_milestone(project_id=resolved, milestone_id=milestone_id, current_user=user))
    return {"deleted": True, "project_id": resolved, "milestone_id": milestone_id}


@tool(category="research", tags=["科研", "计划"], display_name="创建研究任务", args_schema=CreateTaskInput)
async def research_create_task(
    project_id: str | None,
    title: str,
    description: str,
    status: str,
    priority: str,
    due_date: date | None,
    milestone_id: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """在当前项目中创建可跟踪的研究任务。"""
    from yuxi.services.research_project_plan_service import create_project_task

    user = await _current_user(runtime)
    return await _call(
        create_project_task(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            title=title.strip(),
            description=description.strip(),
            status=status,
            priority=priority,
            due_date=due_date,
            milestone_id=milestone_id,
        )
    )


@tool(category="research", tags=["科研", "计划"], display_name="更新研究任务", args_schema=UpdateTaskInput)
async def research_update_task(
    project_id: str | None,
    task_id: str,
    changes: TaskChanges,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """更新当前项目中研究任务的内容、状态、优先级、日期或所属里程碑。"""
    from yuxi.services.research_project_plan_service import update_project_task

    user = await _current_user(runtime)
    return await _call(
        update_project_task(
            project_id=await _project_id(runtime, project_id, user),
            task_id=task_id,
            current_user=user,
            values=changes.model_dump(exclude_unset=True),
        )
    )


@tool(category="research", tags=["科研", "计划", "敏感"], display_name="删除研究任务", args_schema=DeleteTaskInput)
async def research_delete_task(
    project_id: str | None,
    task_id: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """删除当前项目中的研究任务。此操作需要用户审批。"""
    from yuxi.services.research_project_plan_service import delete_project_task

    user = await _current_user(runtime)
    resolved = await _project_id(runtime, project_id, user)
    await _call(delete_project_task(project_id=resolved, task_id=task_id, current_user=user))
    return {"deleted": True, "project_id": resolved, "task_id": task_id}


@tool(category="research", tags=["科研", "检索"], display_name="执行论文检索", args_schema=SearchPapersInput)
async def research_search_papers(
    query: str,
    retrieval_mode: str,
    top_k: int,
    recall_top_k: int,
    year_from: int | None,
    year_to: int | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """在当前学术知识库执行可追溯的混合论文检索，并保存检索运行记录。"""
    from yuxi.services.research_search_service import search_papers

    context = _research_context(runtime)
    user = await _current_user(runtime)
    model = str(getattr(getattr(runtime, "context", None), "model", "") or "").strip() or None
    return await _call(
        search_papers(
            kb_id=context["kb_id"],
            current_user=user,
            query=query,
            top_k=top_k,
            recall_top_k=recall_top_k,
            year_from=year_from,
            year_to=year_to,
            chat_model=model,
            reranker_model=None,
            retrieval_mode=retrieval_mode,
        )
    )


@tool(category="research", tags=["科研", "检索"], display_name="读取检索结果", args_schema=RunReferenceInput)
async def research_get_search_run(run_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """读取属于当前用户的论文检索运行和证据结果。"""
    from yuxi.services.research_search_service import get_search_run

    user = await _current_user(runtime)
    result = await _call(get_search_run(run_id=run_id, current_user=user))
    _ensure_context_kb(runtime, result.get("kb_id"), "检索运行")
    return result


@tool(category="research", tags=["科研", "综述"], display_name="创建证据综述", args_schema=CreateSynthesisInput)
async def research_create_synthesis(
    query: str,
    top_k: int,
    recall_top_k: int,
    year_from: int | None,
    year_to: int | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """基于当前知识库创建异步证据约束综述，返回可跟踪的运行和任务 ID。"""
    from yuxi.services.research_synthesis_service import enqueue_research_synthesis

    context = _research_context(runtime)
    user = await _current_user(runtime)
    model = str(getattr(getattr(runtime, "context", None), "model", "") or "").strip() or None
    return await _call(
        enqueue_research_synthesis(
            kb_id=context["kb_id"],
            current_user=user,
            query=query,
            top_k=top_k,
            recall_top_k=recall_top_k,
            year_from=year_from,
            year_to=year_to,
            model_spec=model,
            reranker_model=None,
        )
    )


@tool(category="research", tags=["科研", "综述"], display_name="读取综述状态", args_schema=RunReferenceInput)
async def research_get_synthesis(run_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """读取证据综述的阶段、状态和已生成结果。"""
    from yuxi.services.research_synthesis_service import get_research_synthesis

    user = await _current_user(runtime)
    result = await _call(get_research_synthesis(run_id=run_id, current_user=user))
    _ensure_context_kb(runtime, result.get("kb_id"), "综述运行")
    return result


@tool(category="research", tags=["科研", "综述"], display_name="重新生成证据综述", args_schema=RunReferenceInput)
async def research_regenerate_synthesis(run_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """使用原综述的检索和模型配置启动一次新的生成。"""
    from yuxi.services.research_synthesis_service import (
        get_research_synthesis,
        regenerate_research_synthesis,
    )

    user = await _current_user(runtime)
    source = await _call(get_research_synthesis(run_id=run_id, current_user=user))
    _ensure_context_kb(runtime, source.get("kb_id"), "综述运行")
    return await _call(regenerate_research_synthesis(run_id=run_id, current_user=user))


@tool(
    category="research",
    tags=["科研", "综述", "导出"],
    display_name="导出证据综述为文件",
    args_schema=ExportSynthesisInput,
)
async def research_export_synthesis(
    run_id: str,
    format: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """将已验证成功的证据综述导出为 Markdown 或 DOCX 文件，写入交付物目录供用户下载。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    from yuxi.agents.backends.sandbox.paths import (
        ensure_thread_dirs,
        sandbox_outputs_dir,
    )
    from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS
    from yuxi.services.research_synthesis_service import (
        export_research_synthesis,
        get_research_synthesis,
    )

    user = await _current_user(runtime)
    source = await _call(get_research_synthesis(run_id=run_id, current_user=user))
    _ensure_context_kb(runtime, source.get("kb_id"), "综述运行")

    filename, content, media_type = await _call(
        export_research_synthesis(run_id=run_id, current_user=user, export_format=format)
    )

    context = getattr(runtime, "context", None)
    thread_id = getattr(context, "file_thread_id", None) or getattr(context, "thread_id", None)
    uid = getattr(context, "uid", None)
    if not thread_id or not uid:
        raise ToolException("当前运行时缺少线程或用户标识，无法保存导出文件")

    ensure_thread_dirs(thread_id, str(uid))
    file_path = sandbox_outputs_dir(thread_id) / filename
    file_path.write_bytes(content)

    artifact_path = f"{VIRTUAL_PATH_OUTPUTS}/{filename}"
    return {
        "run_id": run_id,
        "filename": filename,
        "format": format,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": f"综述已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "成果"],
    display_name="查询可归集成果",
    args_schema=ListAssetCandidatesInput,
)
async def research_list_project_asset_candidates(
    project_id: str | None,
    asset_type: str,
    query: str | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """查询当前项目可归集但尚未加入的论文、检索、综述、分析或评测成果。"""
    from yuxi.services.research_project_service import list_project_asset_candidates

    user = await _current_user(runtime)
    return await _call(
        list_project_asset_candidates(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            asset_type=asset_type,
            query=query,
            offset=0,
            limit=limit,
        )
    )


@tool(category="research", tags=["科研", "成果"], display_name="归集项目成果", args_schema=AddProjectAssetsInput)
async def research_add_project_assets(
    project_id: str | None,
    asset_type: str,
    reference_ids: list[str],
    notes: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把已有论文、检索、综述、分析或评测结果加入当前研究项目。"""
    from yuxi.services.research_project_service import add_project_assets

    user = await _current_user(runtime)
    return await _call(
        add_project_assets(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            asset_type=asset_type,
            reference_ids=reference_ids,
            notes=notes.strip(),
        )
    )


@tool(category="research", tags=["科研", "成果"], display_name="查询项目成果", args_schema=ListProjectAssetsInput)
async def research_list_project_assets(
    project_id: str | None,
    asset_type: str | None,
    query: str | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """查询当前项目已归集的研究成果。"""
    from yuxi.services.research_project_service import list_project_assets

    user = await _current_user(runtime)
    return await _call(
        list_project_assets(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            asset_type=asset_type,
            query=query,
            offset=0,
            limit=limit,
        )
    )


@tool(
    category="research",
    tags=["科研", "成果", "敏感"],
    display_name="移除项目成果",
    args_schema=ProjectAssetInput,
)
async def research_remove_project_asset(
    project_id: str | None,
    asset_id: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """从当前项目移除成果记录。此操作需要用户审批。"""
    from yuxi.services.research_project_service import remove_project_asset

    user = await _current_user(runtime)
    return await _call(
        remove_project_asset(
            project_id=await _project_id(runtime, project_id, user),
            asset_id=asset_id,
            current_user=user,
        )
    )


@tool(category="research", tags=["科研", "成果", "计划"], display_name="关联成果到计划", args_schema=LinkAssetInput)
async def research_link_asset_to_plan(
    project_id: str | None,
    asset_id: str,
    milestone_id: str | None,
    task_id: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把项目成果关联到一个里程碑或任务。"""
    from yuxi.services.research_project_plan_service import create_project_plan_asset_link

    user = await _current_user(runtime)
    return await _call(
        create_project_plan_asset_link(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            asset_id=asset_id,
            milestone_id=milestone_id,
            task_id=task_id,
        )
    )


@tool(
    category="research",
    tags=["科研", "成果", "计划", "敏感"],
    display_name="解除成果计划关联",
    args_schema=UnlinkAssetInput,
)
async def research_unlink_asset_from_plan(
    project_id: str | None,
    link_id: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """解除成果与里程碑或任务的关联。此操作需要用户审批。"""
    from yuxi.services.research_project_plan_service import delete_project_plan_asset_link

    user = await _current_user(runtime)
    resolved = await _project_id(runtime, project_id, user)
    await _call(delete_project_plan_asset_link(project_id=resolved, link_id=link_id, current_user=user))
    return {"deleted": True, "project_id": resolved, "link_id": link_id}


RESEARCH_TOOLS = [
    research_get_context,
    research_list_projects,
    research_get_project,
    research_create_project,
    research_update_project,
    research_set_project_status,
    research_delete_project,
    research_get_project_plan,
    research_create_milestone,
    research_update_milestone,
    research_delete_milestone,
    research_create_task,
    research_update_task,
    research_delete_task,
    research_search_papers,
    research_get_search_run,
    research_create_synthesis,
    research_get_synthesis,
    research_regenerate_synthesis,
    research_export_synthesis,
    research_list_project_asset_candidates,
    research_add_project_assets,
    research_list_project_assets,
    research_remove_project_asset,
    research_link_asset_to_plan,
    research_unlink_asset_from_plan,
]
