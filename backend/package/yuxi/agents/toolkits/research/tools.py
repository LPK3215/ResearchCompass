"""ResearchCompass 科研领域工具集。

本模块是本仓库在开源智能体框架 Yuxi 的工具体系之上，针对科研场景实现的领域工具集。
工具通过 Yuxi 的 ``@tool`` 注册机制挂载到智能体运行态，负责研究项目治理、论文检索、
证据综述、成果归集等科研业务流程；通用智能体运行时、沙盒与中间件由 Yuxi 提供。
"""

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


class RunPaperAnalysisInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="论文 ID，可从检索结果或论文库获得")
    model_spec: str | None = Field(default=None, max_length=512, description="分析模型；省略时使用当前会话模型")


class GetPaperAnalysisInput(BaseModel):
    paper_id: str | None = Field(default=None, max_length=128, description="读取该论文最近一次分析")
    run_id: str | None = Field(default=None, max_length=64, description="读取指定分析运行")

    @model_validator(mode="after")
    def require_target(self):
        if bool(self.paper_id) == bool(self.run_id):
            raise ValueError("必须且只能提供 paper_id 或 run_id")
        return self


class AskPaperQuestionInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="当前知识库内的论文 ID")
    question: str = Field(min_length=1, max_length=2000, description="针对该论文的问题")


class TranslatePaperInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="当前知识库内的论文 ID")
    section_type: str | None = Field(
        default=None, max_length=64, description="只翻译某一章节，如 abstract / method / experiment"
    )
    limit: int = Field(default=6, ge=1, le=12, description="按原文顺序翻译前 N 个片段")


class ListPaperNotesInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="当前知识库内的论文 ID")


class CreatePaperNoteInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="当前知识库内的论文 ID")
    content: str = Field(min_length=1, max_length=10_000, description="笔记正文，必须来自用户明确给出的内容")
    quote: str | None = Field(default=None, max_length=5_000, description="可选：原文引用")
    chunk_id: str | None = Field(default=None, max_length=128, description="可选：批注对应的分块 ID")
    page: int | None = Field(default=None, ge=1, description="可选：PDF 页码")
    start_offset: int | None = Field(default=None, ge=0, description="可选：分块内选区起点")
    end_offset: int | None = Field(default=None, ge=0, description="可选：分块内选区终点")


class UpdatePaperNoteInput(BaseModel):
    note_id: str = Field(min_length=1, max_length=64, description="笔记 ID")
    content: str = Field(min_length=1, max_length=10_000, description="修改后的笔记正文")


class DeletePaperNoteInput(BaseModel):
    note_id: str = Field(min_length=1, max_length=64, description="笔记 ID")


class ListRelatedPapersInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="当前知识库内的论文 ID")
    direction: Literal["references", "citations"] = Field(default="references", description="参考文献或被引文献")
    limit: int = Field(default=20, ge=1, le=50)


class ImportRelatedPapersInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="发起滚雪球的库内论文 ID")
    direction: Literal["references", "citations"]
    paper_ids: list[str] = Field(min_length=1, max_length=50, description="勾选待导入的 Semantic Scholar 论文 ID")
    include_full_text: bool = Field(default=False, description="是否对开放获取条目同时下载全文")


class ExportProjectReportInput(BaseModel):
    format: Literal["markdown", "docx", "csv"] = Field(default="markdown", description="导出格式")


class ExportPapersInput(BaseModel):
    format: Literal["bibtex", "markdown", "ris", "csv", "text", "gb7714", "apa", "ieee"] = Field(
        default="bibtex", description="导出格式，gb7714/apa/ieee 为期刊引文样式"
    )
    paper_ids: list[str] = Field(default_factory=list, max_length=200, description="为空表示导出当前知识库全部论文")


class ImportReferenceFileInput(BaseModel):
    content: str = Field(min_length=1, max_length=200_000, description="BibTeX 或 RIS 文件全文，单次最多 1000 条")
    format: Literal["bibtex", "ris", "auto"] = Field(default="auto", description="文件格式，auto 表示自动识别")


class ListEvidenceInput(BaseModel):
    status: Literal["unverified", "verified", "invalid", "archived"] | None = Field(
        default=None, description="按状态筛选"
    )
    limit: int = Field(default=50, ge=1, le=200)


class CreateEvidenceInput(BaseModel):
    chunk_id: str = Field(min_length=1, max_length=128, description="知识片段 ID")


class TransitionEvidenceInput(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    status: Literal["verified", "invalid", "archived"]
    reason: str | None = Field(default=None, max_length=2000)


class EvidenceReferenceInput(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=64)


class SyncAcademicGraphInput(BaseModel):
    paper_ids: list[str] = Field(default_factory=list, max_length=500, description="为空表示同步当前知识库全部论文")
    citation_limit: int = Field(default=100, ge=0, le=5000, description="每篇论文的引用上限；0 表示仅同步节点")
    reference_limit: int = Field(default=100, ge=0, le=5000, description="每篇论文的参考文献上限；0 表示仅同步节点")


class GraphNetworkInput(BaseModel):
    center_paper_id: str | None = Field(default=None, max_length=128, description="中心论文 ID；省略时返回整体网络")
    depth: int = Field(default=2, ge=1, le=3)
    limit: int = Field(default=200, ge=1, le=1000)


class GraphRelationsInput(BaseModel):
    graph_paper_id: str = Field(min_length=1, max_length=128, description="图谱论文节点 ID")
    limit: int = Field(default=20, ge=1, le=100)


class GraphConflictsInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=64, description="图谱同步运行 ID")
    resolution_status: str | None = Field(default=None, max_length=32)
    limit: int = Field(default=50, ge=1, le=200)


class TrendsInput(BaseModel):
    year_from: int | None = Field(default=None, ge=1500)
    year_to: int | None = Field(default=None, ge=1500)
    top_keywords: int = Field(default=10, ge=1, le=50)
    limit: int = Field(default=20, ge=1, le=100)


class OpportunitiesInput(BaseModel):
    year_from: int | None = Field(default=None, ge=1500)
    year_to: int | None = Field(default=None, ge=1500)
    limit: int = Field(default=10, ge=1, le=50)


class ListRisksInput(ProjectReferenceInput):
    limit: int = Field(default=50, ge=1, le=100)


class CreateRiskInput(ProjectReferenceInput):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=12000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    owner_uid: str | None = Field(default=None, max_length=64, description="责任人 UID；省略时为当前用户")
    mitigation: str = Field(default="", max_length=12000)
    due_date: date | None = None


class TransitionRiskInput(ProjectReferenceInput):
    risk_id: str = Field(min_length=1, max_length=64)
    status: Literal["open", "mitigating", "accepted", "resolved", "closed"]
    mitigation: str | None = Field(default=None, max_length=12000)
    reason: str | None = Field(default=None, max_length=2000)


class RiskReferenceInput(ProjectReferenceInput):
    risk_id: str = Field(min_length=1, max_length=64)


class AddTaskDependencyInput(ProjectReferenceInput):
    task_id: str = Field(min_length=1, max_length=64)
    depends_on_task_id: str = Field(min_length=1, max_length=64)


class TaskDependencyInput(ProjectReferenceInput):
    dependency_id: str = Field(min_length=1, max_length=64)


class CreateUserStudyInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    participant_count: int = Field(default=5, ge=3, le=20)
    consent_text: str | None = Field(default=None, max_length=4000)


class UserStudyInput(BaseModel):
    study_id: str = Field(min_length=1, max_length=64)


class UserStudyReportInput(UserStudyInput):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class CreateAnalysisEvaluationInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    paper_ids: list[str] = Field(min_length=2, max_length=20)
    model_spec: str | None = Field(default=None, max_length=512, description="对比所用模型；省略时使用当前会话模型")


class AnalysisEvaluationInput(BaseModel):
    evaluation_id: str = Field(min_length=1, max_length=64)


class SearchExternalPapersInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000, description="标题、DOI 或关键词")
    limit: int = Field(default=5, ge=1, le=20)


class ImportExternalPaperInput(BaseModel):
    identifier: str = Field(min_length=1, max_length=500, description="论文标题或 DOI")


class BackfillPaperFullTextInput(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=50, description="需要补全文的论文 ID（仅有元数据的条目）")


class RunEvalInput(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=100)


class UploadEvalDatasetInput(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="评估基准名称")
    content: str = Field(
        min_length=2,
        max_length=2_000_000,
        description="JSONL 文本，每行一个对象，至少包含 question 字段，可选 gold_chunks / gold_answer",
    )
    description: str = Field(default="", max_length=2000)
    filename: str = Field(default="agent_eval_dataset.jsonl", max_length=200, description="必须以 .jsonl 结尾")


class EvalExperimentVariantInput(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="变体名称，如 baseline / no_reranker")
    retrieval_config: dict[str, Any] = Field(
        default_factory=dict, description="检索参数覆盖，如 top_k / search_mode / use_reranker"
    )
    kb_id: str | None = Field(
        default=None, max_length=80, description="对照知识库；默认当前库，要求已索引语料与嵌入模型完全一致"
    )


class CreateEvalExperimentInput(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    baseline_variant_index: int = Field(default=0, ge=0, description="作为基线的变体序号，从 0 开始")
    shared_config: dict[str, Any] = Field(default_factory=dict, description="所有变体共享的模型配置")
    variants: list[EvalExperimentVariantInput] = Field(min_length=2, max_length=8)


class EvalRunInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class EvalExperimentInput(BaseModel):
    experiment_id: str = Field(min_length=1, max_length=64)


class ExportPaperCardsInput(BaseModel):
    paper_ids: list[str] = Field(default_factory=list, max_length=200, description="为空表示导出当前知识库全部论文")
    format: Literal["markdown", "json"] = Field(default="markdown", description="导出格式")


class ExportPaperAnalysisInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=64, description="论文分析运行 ID")


class PaperBriefInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="论文 ID")
    model_spec: str | None = Field(default=None, max_length=512, description="生成模型；省略时使用默认模型")


class PresentationPackInput(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=10, description="1 到 10 篇论文 ID")
    format: Literal["markdown", "html"] = Field(default="markdown", description="导出格式")
    model_spec: str | None = Field(default=None, max_length=512, description="生成模型；省略时使用默认模型")


class ExportAcademicGraphInput(BaseModel):
    format: Literal["csv", "graphml"] = Field(default="csv", description="导出格式")
    limit: int = Field(default=500, ge=1, le=500, description="最多导出的图谱节点数")


class StructureDiagramInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128, description="论文 ID")
    kind: Literal["method", "topics"] = Field(default="method", description="method 为方法流程，topics 为主题图")


class ExportResearchDataInput(BaseModel):
    kind: Literal["eval_run", "eval_experiment", "trends", "opportunities"] = Field(
        description="评估运行 / 消融实验 / 趋势 / 机会"
    )
    format: Literal["json", "csv"] = Field(default="json", description="导出格式")
    target_id: str | None = Field(default=None, max_length=128, description="eval_run 或 eval_experiment 的 ID")
    year_from: int | None = Field(default=None, ge=1500)
    year_to: int | None = Field(default=None, ge=1500)


class ExportComparisonMatrixInput(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=20, description="2 到 20 篇论文 ID")
    format: Literal["markdown", "csv"] = Field(default="markdown", description="导出格式")


class CitationInsightInput(BaseModel):
    dummy: str = Field(default="", description="无需填写，忽略此参数")


class SynthesisTopicsInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=64, description="证据综述运行 ID")


class SynthesisTopicTasksInput(SynthesisTopicsInput):
    limit: int = Field(default=10, ge=1, le=20, description="最多创建的选题任务数")


class GenerateEvalDatasetInput(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="基准名称")
    count: int = Field(default=10, ge=5, le=50, description="题目数量")
    llm_model_spec: str | None = Field(default=None, max_length=512, description="生成模型；省略时使用默认模型")
    description: str = Field(default="", max_length=500, description="基准说明")


class DuplicateMergeInput(BaseModel):
    key: str = Field(min_length=1, max_length=600, description="查重返回的分组 key")


class ValidateKbManifestInput(BaseModel):
    manifest_json: str = Field(
        min_length=2,
        max_length=2_000_000,
        description="知识库迁移清单 JSON 文本（由 research_export_kb_manifest 导出）",
    )


class DuplicateRestoreInput(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=50, description="待恢复的论文 ID")


class EnrichPaperMetadataInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64, description="要补全元数据的论文 ID")


class CrossLibraryMergeInput(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64, description="当前知识库中要保留的论文 ID")
    source_kb_id: str = Field(min_length=1, max_length=80, description="对方知识库 ID（重复记录所在库）")
    source_paper_id: str = Field(min_length=1, max_length=64, description="对方知识库中的重复论文 ID")


class GetPaperScreeningInput(BaseModel):
    decision: Literal["included", "excluded", "pending"] | None = Field(
        default=None, description="只看某一类筛选结论；不传则返回全部"
    )
    limit: int = Field(default=50, ge=1, le=200, description="最多返回多少篇论文")


class SetPaperScreeningInput(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=200, description="要标记的论文 ID 列表")
    decision: Literal["included", "excluded", "pending"] = Field(description="纳入、排除或退回待定")
    reason: str = Field(default="", max_length=500, description="筛选理由，来自用户的判断依据")


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
    except ValueError as exc:
        raise ToolException(str(exc)) from exc
    except Exception as exc:
        message = getattr(exc, "message", None)
        if isinstance(message, str) and message.strip():
            raise ToolException(message) from exc
        raise


async def _write_artifact(runtime: ToolRuntime, filename: str, content: bytes) -> str:
    from yuxi.agents.backends.sandbox.paths import ensure_thread_dirs, sandbox_outputs_dir
    from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS

    context = getattr(runtime, "context", None)
    thread_id = getattr(context, "file_thread_id", None) or getattr(context, "thread_id", None)
    uid = getattr(context, "uid", None)
    if not thread_id or not uid:
        raise ToolException("当前运行时缺少线程或用户标识，无法保存导出文件")
    ensure_thread_dirs(thread_id, str(uid))
    file_path = sandbox_outputs_dir(thread_id) / filename
    file_path.write_bytes(content)
    return f"{VIRTUAL_PATH_OUTPUTS}/{filename}"


def _require_admin(user: Any) -> None:
    if str(getattr(user, "role", "") or "") not in {"admin", "superadmin"}:
        raise ToolException("RAG 评估相关操作仅管理员可用")


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
    from yuxi.services.research_synthesis_service import (
        export_research_synthesis,
        get_research_synthesis,
    )
    from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS

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


@tool(category="research", tags=["科研", "分析"], display_name="运行论文分析", args_schema=RunPaperAnalysisInput)
async def research_run_paper_analysis(
    paper_id: str,
    model_spec: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """对当前知识库中的单篇论文发起结构化分析，返回可跟踪的运行 ID。"""
    from yuxi.services.academic_paper_analysis_service import enqueue_paper_analysis

    context = _research_context(runtime)
    user = await _current_user(runtime)
    model = (
        (model_spec or "").strip() or str(getattr(getattr(runtime, "context", None), "model", "") or "").strip() or None
    )
    return await _call(
        enqueue_paper_analysis(
            kb_id=context["kb_id"],
            paper_id=paper_id,
            current_user=user,
            model_spec=model,
        )
    )


@tool(category="research", tags=["科研", "分析"], display_name="读取论文分析", args_schema=GetPaperAnalysisInput)
async def research_get_paper_analysis(
    paper_id: str | None,
    run_id: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取指定分析运行，或某篇论文最近一次分析结果。"""
    from yuxi.services.academic_paper_analysis_service import (
        get_latest_paper_analysis,
        get_paper_analysis_run,
    )

    user = await _current_user(runtime)
    if run_id:
        result = await _call(get_paper_analysis_run(run_id=run_id, current_user=user))
        _ensure_context_kb(runtime, result.get("kb_id"), "论文分析运行")
        return result
    context = _research_context(runtime)
    latest = await _call(get_latest_paper_analysis(kb_id=context["kb_id"], paper_id=paper_id, current_user=user))
    if latest is None:
        return {"run": None, "message": "该论文还没有分析运行，可先调用 research_run_paper_analysis"}
    return latest


@tool(
    category="research",
    tags=["科研", "分析", "阅读"],
    display_name="追问单篇论文",
    args_schema=AskPaperQuestionInput,
)
async def research_ask_paper_question(paper_id: str, question: str, runtime: ToolRuntime) -> dict[str, Any]:
    """在单篇论文范围内追问，回答附带 chunk 引用与页码，与论文分析页对话同一检索范围。"""
    from yuxi.services.research_paper_chat_service import ask_paper_question

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        ask_paper_question(kb_id=context["kb_id"], paper_id=paper_id, question=question, current_user=user)
    )


@tool(
    category="research",
    tags=["科研", "阅读", "翻译"],
    display_name="论文对照翻译",
    args_schema=TranslatePaperInput,
)
async def research_translate_paper(
    paper_id: str,
    section_type: str | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """生成论文片段的逐段中英对照（带页码与章节）；译文不落库，不能当作引用证据。"""
    from yuxi.services.research_paper_translation_service import translate_paper

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        translate_paper(
            kb_id=context["kb_id"],
            paper_id=paper_id,
            current_user=user,
            section_type=section_type,
            limit=limit,
        )
    )


@tool(category="research", tags=["科研", "阅读", "笔记"], display_name="查询论文笔记", args_schema=ListPaperNotesInput)
async def research_list_paper_notes(paper_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """读取当前用户在某篇论文上的阅读笔记与批注。"""
    from yuxi.services.research_paper_note_service import list_paper_notes

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(list_paper_notes(kb_id=context["kb_id"], paper_id=paper_id, current_user=user))


@tool(category="research", tags=["科研", "阅读", "笔记"], display_name="记录论文笔记", args_schema=CreatePaperNoteInput)
async def research_create_paper_note(
    paper_id: str,
    content: str,
    quote: str | None,
    chunk_id: str | None,
    page: int | None,
    start_offset: int | None,
    end_offset: int | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """在单篇论文下记录阅读笔记；批注可附带分块 ID、PDF 页码与分块内字符区间。

    笔记正文必须来自用户明确给出的文字或原文引用，不得替用户编造阅读结论。
    """
    from yuxi.services.research_paper_note_service import create_paper_note

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        create_paper_note(
            kb_id=context["kb_id"],
            paper_id=paper_id,
            content=content,
            quote=quote,
            chunk_id=chunk_id,
            page=page,
            start_offset=start_offset,
            end_offset=end_offset,
            current_user=user,
        )
    )


@tool(category="research", tags=["科研", "阅读", "笔记"], display_name="修改论文笔记", args_schema=UpdatePaperNoteInput)
async def research_update_paper_note(note_id: str, content: str, runtime: ToolRuntime) -> dict[str, Any]:
    """修改当前用户自己的一条论文笔记。"""
    from yuxi.services.research_paper_note_service import update_paper_note

    user = await _current_user(runtime)
    return await _call(update_paper_note(note_id=note_id, content=content, current_user=user))


@tool(
    category="research",
    tags=["科研", "阅读", "笔记", "敏感"],
    display_name="删除论文笔记",
    args_schema=DeletePaperNoteInput,
)
async def research_delete_paper_note(note_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """删除当前用户自己的一条论文笔记，需审批。"""
    from yuxi.services.research_paper_note_service import delete_paper_note

    user = await _current_user(runtime)
    return await _call(delete_paper_note(note_id=note_id, current_user=user))


@tool(
    category="research",
    tags=["科研", "阅读", "笔记", "导出"],
    display_name="导出论文笔记",
    args_schema=ListPaperNotesInput,
)
async def research_export_paper_notes(paper_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """把当前用户在某篇论文上的笔记导出为 Markdown，并调用 present_artifacts 展示。"""
    from yuxi.services.research_paper_note_service import export_paper_notes_markdown

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(
        export_paper_notes_markdown(kb_id=context["kb_id"], paper_id=paper_id, current_user=user)
    )
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "paper_id": paper_id,
        "filename": filename,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": f"论文笔记已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "引文", "发现"],
    display_name="列出关联文献",
    args_schema=ListRelatedPapersInput,
)
async def research_list_related_papers(
    paper_id: str,
    direction: str,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """列出某篇库内论文的参考文献或被引文献，并标注是否已在库内。"""
    from yuxi.services.research_citation_snowball_service import list_related_papers

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        list_related_papers(
            kb_id=context["kb_id"], paper_id=paper_id, direction=direction, current_user=user, limit=limit
        )
    )


@tool(
    category="research",
    tags=["科研", "引文", "导入"],
    display_name="滚雪球导入关联文献",
    args_schema=ImportRelatedPapersInput,
)
async def research_import_related_papers(
    paper_id: str,
    direction: str,
    paper_ids: list[str],
    include_full_text: bool,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把勾选的关联文献纳入本地库：默认只写元数据，开放获取条目可选下载全文。"""
    from yuxi.services.research_citation_snowball_service import import_related_papers

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        import_related_papers(
            kb_id=context["kb_id"],
            paper_id=paper_id,
            direction=direction,
            paper_ids=paper_ids,
            include_full_text=include_full_text,
            current_user=user,
        )
    )


@tool(
    category="research",
    tags=["科研", "项目", "导出"],
    display_name="导出项目报告",
    args_schema=ExportProjectReportInput,
)
async def research_export_project_report(format: str, runtime: ToolRuntime) -> dict[str, Any]:
    """将当前研究项目导出为 Markdown、Word 或 CSV 报告，写入交付物目录供用户下载。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    from yuxi.services.research_project_report_service import export_research_project_report

    user = await _current_user(runtime)
    project_id = await _project_id(runtime, None, user)
    filename, content, media_type = await _call(
        export_research_project_report(project_id=project_id, current_user=user, export_format=format)
    )
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "project_id": project_id,
        "filename": filename,
        "format": format,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": f"项目报告已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "论文", "导出"],
    display_name="导出论文元数据",
    args_schema=ExportPapersInput,
)
async def research_export_papers(
    format: str,
    paper_ids: list[str],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """将当前知识库论文导出为 BibTeX、Markdown、RIS 或 CSV，写入交付物目录供用户下载。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    from yuxi.services.research_paper_export_service import export_papers_document

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(
        export_papers_document(
            kb_id=context["kb_id"],
            current_user=user,
            paper_ids=paper_ids or None,
            export_format=format,
        )
    )
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "filename": filename,
        "format": format,
        "media_type": media_type,
        "paper_count": len(paper_ids) if paper_ids else None,
        "artifact_path": artifact_path,
        "message": f"论文元数据已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "文献", "导入"],
    display_name="导入参考文献文件",
    args_schema=ImportReferenceFileInput,
)
async def research_import_reference_file(content: str, format: str, runtime: ToolRuntime) -> dict[str, Any]:
    """把 BibTeX 或 RIS 文件导入当前知识库，按 DOI 优先、标题兜底去重。

    只导入元数据，不下载全文；导入后的论文可在论文库查看，需要全文时按 DOI 导入外部论文。
    """
    from yuxi.services.research_reference_import_service import import_references

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        import_references(
            kb_id=context["kb_id"],
            content=content,
            reference_format=None if format == "auto" else format,
            current_user=user,
        )
    )


@tool(category="research", tags=["科研", "筛选"], display_name="查询文献筛选", args_schema=GetPaperScreeningInput)
async def research_get_paper_screening(
    decision: str | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取当前知识库的纳入 / 排除 / 待定计数与逐篇筛选结果。"""
    from yuxi.services.research_paper_screening_service import get_paper_screening

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(get_paper_screening(kb_id=context["kb_id"], current_user=user, decision=decision, limit=limit))


@tool(
    category="research",
    tags=["科研", "筛选", "敏感"],
    display_name="记录文献筛选结论",
    args_schema=SetPaperScreeningInput,
)
async def research_set_paper_screening(
    paper_ids: list[str],
    decision: str,
    reason: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """按用户已明确给出的结论批量记录纳入 / 排除；不得自行判断论文质量。此操作需要用户审批。"""
    from yuxi.services.research_paper_screening_service import set_paper_screening

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        set_paper_screening(
            kb_id=context["kb_id"], paper_ids=paper_ids, decision=decision, reason=reason, current_user=user
        )
    )


@tool(
    category="research",
    tags=["科研", "筛选", "导出"],
    display_name="导出文献筛选结果",
    args_schema=EmptyInput,
)
async def research_export_paper_screening(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """把当前用户的逐篇筛选结果导出为 CSV，写入交付物目录供用户下载。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    del dummy
    from yuxi.services.research_paper_screening_service import (
        export_screening_csv,
        get_paper_screening,
    )

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(export_screening_csv(kb_id=context["kb_id"], current_user=user))
    counts = (await _call(get_paper_screening(kb_id=context["kb_id"], current_user=user, limit=1)))["counts"]
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "filename": filename,
        "media_type": media_type,
        "counts": counts,
        "artifact_path": artifact_path,
        "message": f"筛选结果已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(category="research", tags=["科研", "证据"], display_name="查询证据", args_schema=ListEvidenceInput)
async def research_list_evidence(
    status: str | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """查询当前知识库已录入的证据及其验证状态。"""
    from yuxi.services.research_evidence_service import list_evidence

    context = _research_context(runtime)
    user = await _current_user(runtime)
    items = await _call(list_evidence(kb_id=context["kb_id"], status=status, current_user=user))
    return {"items": items[:limit], "total": len(items)}


@tool(category="research", tags=["科研", "证据"], display_name="录入证据", args_schema=CreateEvidenceInput)
async def research_create_evidence(chunk_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """把一个知识片段录入为待验证证据；录入后需调用 research_verify_evidence 完成验证。"""
    from yuxi.services.research_evidence_service import create_evidence

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(create_evidence(kb_id=context["kb_id"], chunk_id=chunk_id, current_user=user))


@tool(
    category="research",
    tags=["科研", "证据", "敏感"],
    display_name="变更证据状态",
    args_schema=TransitionEvidenceInput,
)
async def research_verify_evidence(
    evidence_id: str,
    status: str,
    reason: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """验证、作废或归档一条证据；证据综述失败于证据校验时可用该工具补齐。此操作需要用户审批。"""
    from yuxi.services.research_evidence_service import transition_evidence

    user = await _current_user(runtime)
    return await _call(transition_evidence(evidence_id=evidence_id, status=status, reason=reason, current_user=user))


@tool(category="research", tags=["科研", "证据"], display_name="读取证据审计", args_schema=EvidenceReferenceInput)
async def research_get_evidence_audit(evidence_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """读取一条证据的状态活动、被综述引用记录与项目影响范围。"""
    from yuxi.services.research_evidence_service import (
        list_evidence_activities,
        list_evidence_citations,
        list_evidence_impacts,
    )

    user = await _current_user(runtime)
    activities = await _call(list_evidence_activities(evidence_id=evidence_id, current_user=user))
    citations = await _call(list_evidence_citations(evidence_id=evidence_id, current_user=user))
    impacts = await _call(list_evidence_impacts(evidence_id=evidence_id, current_user=user))
    return {"evidence_id": evidence_id, "activities": activities, "citations": citations, "impacts": impacts}


@tool(
    category="research",
    tags=["科研", "图谱"],
    display_name="同步引用图谱",
    args_schema=SyncAcademicGraphInput,
)
async def research_sync_academic_graph(
    paper_ids: list[str],
    citation_limit: int,
    reference_limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把当前知识库论文同步到学术引用图谱，返回可跟踪的同步运行 ID。"""
    from yuxi.services.academic_graph_sync_service import enqueue_academic_graph_sync

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        enqueue_academic_graph_sync(
            kb_id=context["kb_id"],
            current_user=user,
            paper_ids=paper_ids,
            citation_limit=citation_limit,
            reference_limit=reference_limit,
        )
    )


@tool(category="research", tags=["科研", "图谱"], display_name="读取引用图谱", args_schema=GraphNetworkInput)
async def research_get_academic_graph(
    center_paper_id: str | None,
    depth: int,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取当前知识库的引用图谱网络，可按中心论文展开指定深度。"""
    from yuxi.services.academic_graph_sync_service import get_academic_graph_network

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        get_academic_graph_network(
            kb_id=context["kb_id"],
            current_user=user,
            center_paper_id=center_paper_id,
            depth=depth,
            limit=limit,
        )
    )


@tool(
    category="research",
    tags=["科研", "图谱"],
    display_name="读取两跳引用关系",
    args_schema=GraphRelationsInput,
)
async def research_get_graph_relations(
    graph_paper_id: str,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取某篇图谱论文的可解释两跳引用关系（含中间节点与边方向）。"""
    from yuxi.services.academic_graph_sync_service import get_academic_graph_relations

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        get_academic_graph_relations(
            kb_id=context["kb_id"],
            current_user=user,
            graph_paper_id=graph_paper_id,
            limit=limit,
        )
    )


@tool(
    category="research",
    tags=["科研", "图谱"],
    display_name="读取图谱同步冲突",
    args_schema=GraphConflictsInput,
)
async def research_list_graph_conflicts(
    run_id: str,
    resolution_status: str | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取一次图谱同步运行中的元数据冲突。"""
    from yuxi.services.academic_graph_sync_service import list_academic_graph_conflicts

    user = await _current_user(runtime)
    return await _call(
        list_academic_graph_conflicts(
            run_id=run_id,
            current_user=user,
            resolution_status=resolution_status,
            offset=0,
            limit=limit,
        )
    )


@tool(category="research", tags=["科研", "趋势"], display_name="读取研究趋势", args_schema=TrendsInput)
async def research_get_trends(
    year_from: int | None,
    year_to: int | None,
    top_keywords: int,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """按年份与关键词统计当前知识库的发表趋势、关键词演进与新兴方向。"""
    from yuxi.services.academic_trend_service import get_academic_trends

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        get_academic_trends(
            kb_id=context["kb_id"],
            current_user=user,
            year_from=year_from,
            year_to=year_to,
            offset=0,
            limit=limit,
            top_keywords=top_keywords,
        )
    )


@tool(category="research", tags=["科研", "机会"], display_name="读取研究机会", args_schema=OpportunitiesInput)
async def research_get_opportunities(
    year_from: int | None,
    year_to: int | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """按增长、近期性、覆盖度与图谱缺口给出当前知识库的研究机会排序。"""
    from yuxi.services.academic_opportunity_service import get_academic_opportunities

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        get_academic_opportunities(
            kb_id=context["kb_id"],
            current_user=user,
            year_from=year_from,
            year_to=year_to,
            limit=limit,
        )
    )


@tool(category="research", tags=["科研", "风险"], display_name="查询项目风险", args_schema=ListRisksInput)
async def research_list_project_risks(
    project_id: str | None,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """查询当前研究项目的风险登记项与处置状态。"""
    from yuxi.services.research_project_risk_service import list_project_risks

    user = await _current_user(runtime)
    items = await _call(list_project_risks(project_id=await _project_id(runtime, project_id, user), current_user=user))
    return {"items": items[:limit], "total": len(items)}


@tool(category="research", tags=["科研", "风险"], display_name="登记项目风险", args_schema=CreateRiskInput)
async def research_create_project_risk(
    project_id: str | None,
    title: str,
    description: str,
    severity: str,
    owner_uid: str | None,
    mitigation: str,
    due_date: date | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """在当前研究项目登记一条风险，可指定责任人与缓解措施。"""
    from yuxi.services.research_project_risk_service import create_project_risk

    user = await _current_user(runtime)
    owner = (owner_uid or "").strip() or str(user.uid)
    return await _call(
        create_project_risk(
            project_id=await _project_id(runtime, project_id, user),
            current_user=user,
            title=title.strip(),
            description=description.strip(),
            severity=severity,
            owner_uid=owner,
            mitigation=mitigation.strip(),
            due_date=due_date,
        )
    )


@tool(
    category="research",
    tags=["科研", "风险", "敏感"],
    display_name="变更风险状态",
    args_schema=TransitionRiskInput,
)
async def research_transition_project_risk(
    project_id: str | None,
    risk_id: str,
    status: str,
    mitigation: str | None,
    reason: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """推进风险处置状态（open/mitigating/accepted/resolved/closed）。此操作需要用户审批。"""
    from yuxi.services.research_project_risk_service import transition_project_risk

    user = await _current_user(runtime)
    return await _call(
        transition_project_risk(
            project_id=await _project_id(runtime, project_id, user),
            risk_id=risk_id,
            status=status,
            current_user=user,
            mitigation=mitigation,
            reason=reason,
        )
    )


@tool(category="research", tags=["科研", "风险"], display_name="查询风险活动", args_schema=RiskReferenceInput)
async def research_list_project_risk_activities(
    project_id: str | None,
    risk_id: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取一条风险的处置活动记录。"""
    from yuxi.services.research_project_risk_service import list_project_risk_activities

    user = await _current_user(runtime)
    items = await _call(
        list_project_risk_activities(
            project_id=await _project_id(runtime, project_id, user),
            risk_id=risk_id,
            current_user=user,
        )
    )
    return {"risk_id": risk_id, "items": items}


@tool(
    category="research",
    tags=["科研", "计划"],
    display_name="添加任务依赖",
    args_schema=AddTaskDependencyInput,
)
async def research_add_task_dependency(
    project_id: str | None,
    task_id: str,
    depends_on_task_id: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """声明一个任务依赖于另一个任务，循环依赖会被拒绝。"""
    from yuxi.services.research_project_plan_service import create_project_task_dependency

    user = await _current_user(runtime)
    return await _call(
        create_project_task_dependency(
            project_id=await _project_id(runtime, project_id, user),
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
            current_user=user,
        )
    )


@tool(
    category="research",
    tags=["科研", "计划", "敏感"],
    display_name="移除任务依赖",
    args_schema=TaskDependencyInput,
)
async def research_remove_task_dependency(
    project_id: str | None,
    dependency_id: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """移除一条任务依赖关系。此操作需要用户审批。"""
    from yuxi.services.research_project_plan_service import delete_project_task_dependency

    user = await _current_user(runtime)
    resolved = await _project_id(runtime, project_id, user)
    await _call(delete_project_task_dependency(project_id=resolved, dependency_id=dependency_id, current_user=user))
    return {"deleted": True, "project_id": resolved, "dependency_id": dependency_id}


@tool(category="research", tags=["科研", "评测"], display_name="查询用户评测", args_schema=EmptyInput)
async def research_list_user_studies(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """查询当前知识库的用户评测、参与人数与回收情况。"""
    del dummy
    from yuxi.services.research_user_study_service import ResearchUserStudyService

    context = _research_context(runtime)
    user = await _current_user(runtime)
    items = await _call(ResearchUserStudyService().list_studies(kb_id=context["kb_id"], current_user=user))
    return {"items": items, "total": len(items)}


@tool(
    category="research",
    tags=["科研", "评测"],
    display_name="创建用户评测",
    args_schema=CreateUserStudyInput,
)
async def research_create_user_study(
    name: str,
    description: str,
    participant_count: int,
    consent_text: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """创建一个匿名单次邀请的用户评测，返回仅展示一次的参与者链接。"""
    from yuxi.services.research_user_study_service import ResearchUserStudyService

    context = _research_context(runtime)
    user = await _current_user(runtime)
    result = await _call(
        ResearchUserStudyService().create_study(
            kb_id=context["kb_id"],
            current_user=user,
            name=name.strip(),
            description=description.strip(),
            participant_count=participant_count,
            consent_text=consent_text,
        )
    )
    return {
        **result,
        "message": "公开邀请链接只在本条结果中出现一次，请立即转交参与者",
    }


@tool(category="research", tags=["科研", "评测"], display_name="读取用户评测报告", args_schema=UserStudyReportInput)
async def research_get_user_study_report(
    study_id: str,
    page: int,
    page_size: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取用户评测的汇总（SUS、任务评分、满意度）与匿名原始响应。"""
    from yuxi.services.research_user_study_service import ResearchUserStudyService

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        ResearchUserStudyService().get_study_report(
            kb_id=context["kb_id"],
            study_id=study_id,
            current_user=user,
            page=page,
            page_size=page_size,
        )
    )


@tool(
    category="research",
    tags=["科研", "评测", "敏感"],
    display_name="关闭用户评测",
    args_schema=UserStudyInput,
)
async def research_close_user_study(study_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """关闭用户评测，停止接收新响应。此操作需要用户审批。"""
    from yuxi.services.research_user_study_service import ResearchUserStudyService

    context = _research_context(runtime)
    user = await _current_user(runtime)
    await _call(ResearchUserStudyService().close_study(kb_id=context["kb_id"], study_id=study_id, current_user=user))
    return {"closed": True, "study_id": study_id}


@tool(
    category="research",
    tags=["科研", "评测", "导出"],
    display_name="导出用户评测 CSV",
    args_schema=UserStudyInput,
)
async def research_export_user_study(study_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """将用户评测的匿名原始响应导出为 CSV，写入交付物目录供用户下载。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    from yuxi.services.research_user_study_service import ResearchUserStudyService

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content = await _call(
        ResearchUserStudyService().export_study_csv(kb_id=context["kb_id"], study_id=study_id, current_user=user)
    )
    artifact_path = await _write_artifact(runtime, filename, content.encode("utf-8-sig"))
    return {
        "study_id": study_id,
        "filename": filename,
        "artifact_path": artifact_path,
        "message": f"评测数据已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "对比"],
    display_name="创建分析对比",
    args_schema=CreateAnalysisEvaluationInput,
)
async def research_create_analysis_evaluation(
    name: str,
    description: str,
    paper_ids: list[str],
    model_spec: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """对 2 至 20 篇论文同时生成单 Agent 与多 Agent 报告，供后续人工 A/B 盲审。"""
    from yuxi.services.academic_paper_analysis_evaluation_service import (
        AcademicPaperAnalysisEvaluationService,
    )

    context = _research_context(runtime)
    user = await _current_user(runtime)
    model = (model_spec or "").strip() or str(getattr(getattr(runtime, "context", None), "model", "") or "").strip()
    if not model:
        raise ToolException("分析对比需要指定模型，请在参数中提供 model_spec")
    return await _call(
        AcademicPaperAnalysisEvaluationService().create_evaluation(
            kb_id=context["kb_id"],
            current_user=user,
            name=name.strip(),
            description=description.strip(),
            paper_ids=paper_ids,
            model_spec=model,
        )
    )


@tool(
    category="research",
    tags=["科研", "对比"],
    display_name="查询分析对比",
    args_schema=EmptyInput,
)
async def research_list_analysis_evaluations(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """查询当前知识库的分析对比实验及其状态。"""
    del dummy
    from yuxi.services.academic_paper_analysis_evaluation_service import (
        AcademicPaperAnalysisEvaluationService,
    )

    context = _research_context(runtime)
    user = await _current_user(runtime)
    items = await _call(
        AcademicPaperAnalysisEvaluationService().list_evaluations(kb_id=context["kb_id"], current_user=user)
    )
    return {"items": items, "total": len(items)}


@tool(
    category="research",
    tags=["科研", "对比"],
    display_name="读取分析对比报告",
    args_schema=AnalysisEvaluationInput,
)
async def research_get_analysis_evaluation(evaluation_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """读取分析对比的汇总报告（仅统计真实人工盲审；无评分时返回无数据）。"""
    from yuxi.services.academic_paper_analysis_evaluation_service import (
        AcademicPaperAnalysisEvaluationService,
    )

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        AcademicPaperAnalysisEvaluationService().get_report(
            kb_id=context["kb_id"], evaluation_id=evaluation_id, current_user=user
        )
    )


@tool(
    category="research",
    tags=["科研", "外部论文"],
    display_name="搜索外部论文",
    args_schema=SearchExternalPapersInput,
)
async def research_search_external_papers(query: str, limit: int, runtime: ToolRuntime) -> dict[str, Any]:
    """按关键词检索 Semantic Scholar、OpenAlex 与 Crossref 并归并去重，返回候选、来源状态与开放获取情况。"""
    from yuxi.services.academic_paper_import_service import search_external_papers

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(search_external_papers(kb_id=context["kb_id"], current_user=user, query=query, limit=limit))


@tool(
    category="research",
    tags=["科研", "外部论文"],
    display_name="导入外部论文",
    args_schema=ImportExternalPaperInput,
)
async def research_import_external_paper(identifier: str, runtime: ToolRuntime) -> dict[str, Any]:
    """按标题或 DOI 将公开论文导入当前知识库并启动解析。"""
    from yuxi.services.academic_paper_import_service import import_external_paper

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(import_external_paper(identifier=identifier.strip(), kb_id=context["kb_id"], current_user=user))


@tool(
    category="research",
    tags=["科研", "外部论文", "补全文"],
    display_name="批量补全文",
    args_schema=BackfillPaperFullTextInput,
)
async def research_backfill_paper_fulltext(paper_ids: list[str], runtime: ToolRuntime) -> dict[str, Any]:
    """为只有元数据的论文批量提交公开全文导入任务，返回已提交、跳过与失败条目。"""
    from yuxi.services.academic_paper_import_service import backfill_paper_full_text

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(backfill_paper_full_text(kb_id=context["kb_id"], paper_ids=paper_ids, current_user=user))


@tool(
    category="research",
    tags=["科研", "评估"],
    display_name="查询评估基准",
    args_schema=EmptyInput,
)
async def research_list_eval_datasets(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """查询当前知识库的 RAG 评估基准（仅管理员）。"""
    del dummy
    from yuxi.knowledge.eval.service import EvaluationService
    from yuxi.services.research_paper_service import _ensure_access

    context = _research_context(runtime)
    user = await _current_user(runtime)
    _require_admin(user)
    await _call(_ensure_access(user, context["kb_id"]))
    items = await _call(EvaluationService().list_datasets(context["kb_id"]))
    return {"items": items, "total": len(items)}


@tool(
    category="research",
    tags=["科研", "评估", "上传"],
    display_name="上传评估基准",
    args_schema=UploadEvalDatasetInput,
)
async def research_upload_eval_dataset(
    name: str,
    content: str,
    description: str,
    filename: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把 JSONL 文本上传为 RAG 评估基准（仅管理员）；文件名必须以 .jsonl 结尾。"""
    if not filename.endswith(".jsonl"):
        raise ToolException("评估基准文件名必须以 .jsonl 结尾")
    from yuxi.knowledge.eval.service import EvaluationService
    from yuxi.services.research_paper_service import _ensure_access

    context = _research_context(runtime)
    user = await _current_user(runtime)
    _require_admin(user)
    await _call(_ensure_access(user, context["kb_id"], write=True))
    result = await _call(
        EvaluationService().upload_dataset(
            kb_id=context["kb_id"],
            file_content=content.encode("utf-8"),
            filename=filename,
            name=name,
            description=description,
            created_by=str(user.uid),
        )
    )
    return {
        "dataset_id": result.get("dataset_id"),
        "name": result.get("name") or name,
        "item_count": result.get("item_count"),
        "has_gold_chunks": result.get("has_gold_chunks"),
        "has_gold_answers": result.get("has_gold_answers"),
        "message": "评估基准已上传，可在 RAG 评估或消融实验中选择该基准",
    }


@tool(
    category="research",
    tags=["科研", "评估", "消融"],
    display_name="创建消融实验",
    args_schema=CreateEvalExperimentInput,
)
async def research_create_eval_experiment(
    dataset_id: str,
    name: str,
    description: str,
    baseline_variant_index: int,
    shared_config: dict[str, Any],
    variants: list[EvalExperimentVariantInput],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """基于已有评估基准创建检索消融实验（仅管理员）；变体均为 ResearchCompass 执行，外部基线仍在界面创建。"""
    from yuxi.knowledge.eval.service import EvaluationService
    from yuxi.services.research_paper_service import _ensure_access

    context = _research_context(runtime)
    user = await _current_user(runtime)
    _require_admin(user)
    await _call(_ensure_access(user, context["kb_id"], write=True))
    result = await _call(
        EvaluationService().create_experiment(
            kb_id=context["kb_id"],
            dataset_id=dataset_id,
            name=name,
            description=description,
            baseline_variant_index=baseline_variant_index,
            shared_config=shared_config,
            variants=[variant.model_dump() for variant in variants],
            created_by=str(user.uid),
        )
    )
    return {
        "experiment_id": result.get("experiment_id"),
        "name": result.get("name") or name,
        "variant_count": len(result.get("variants") or variants),
        "baseline_variant_index": baseline_variant_index,
        "status": result.get("status"),
        "message": "消融实验已创建，请用 research_get_eval_experiment 读取变体对比报告",
    }


@tool(
    category="research",
    tags=["科研", "评估"],
    display_name="发起 RAG 评估",
    args_schema=RunEvalInput,
)
async def research_run_eval(dataset_id: str, name: str | None, runtime: ToolRuntime) -> dict[str, Any]:
    """基于指定评估基准发起一次 RAG 评估（仅管理员）。"""
    from yuxi.knowledge.eval.service import EvaluationService
    from yuxi.services.research_paper_service import _ensure_access

    context = _research_context(runtime)
    user = await _current_user(runtime)
    _require_admin(user)
    await _call(_ensure_access(user, context["kb_id"], write=True))
    run_id = await _call(
        EvaluationService().run_evaluation(
            kb_id=context["kb_id"],
            dataset_id=dataset_id,
            name=name,
            model_config={},
            created_by=str(user.uid),
        )
    )
    return {"run_id": run_id, "status": "queued"}


@tool(
    category="research",
    tags=["科研", "评估"],
    display_name="读取评估结果",
    args_schema=EvalRunInput,
)
async def research_get_eval_run(
    run_id: str,
    page: int,
    page_size: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取一次 RAG 评估的逐题结果与指标（仅管理员）。"""
    from yuxi.knowledge.eval.service import EvaluationService
    from yuxi.services.research_paper_service import _ensure_access

    context = _research_context(runtime)
    user = await _current_user(runtime)
    _require_admin(user)
    await _call(_ensure_access(user, context["kb_id"]))
    return await _call(
        EvaluationService().get_run_results(kb_id=context["kb_id"], run_id=run_id, page=page, page_size=page_size)
    )


@tool(
    category="research",
    tags=["科研", "评估"],
    display_name="查询消融实验",
    args_schema=EmptyInput,
)
async def research_list_eval_experiments(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """查询当前知识库的消融实验列表（仅管理员）。"""
    del dummy
    from yuxi.knowledge.eval.service import EvaluationService
    from yuxi.services.research_paper_service import _ensure_access

    context = _research_context(runtime)
    user = await _current_user(runtime)
    _require_admin(user)
    await _call(_ensure_access(user, context["kb_id"]))
    items = await _call(EvaluationService().list_experiments(context["kb_id"]))
    return {"items": items, "total": len(items)}


@tool(
    category="research",
    tags=["科研", "评估"],
    display_name="读取消融实验报告",
    args_schema=EvalExperimentInput,
)
async def research_get_eval_experiment(experiment_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """读取消融实验的变体对比报告与数据集/语料指纹（仅管理员）。"""
    from yuxi.knowledge.eval.service import EvaluationService
    from yuxi.services.research_paper_service import _ensure_access

    context = _research_context(runtime)
    user = await _current_user(runtime)
    _require_admin(user)
    await _call(_ensure_access(user, context["kb_id"]))
    return await _call(EvaluationService().get_experiment(context["kb_id"], experiment_id))


@tool(
    category="research",
    tags=["科研", "论文", "导出"],
    display_name="导出文献卡片",
    args_schema=ExportPaperCardsInput,
)
async def research_export_paper_cards(
    paper_ids: list[str],
    format: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把论文导出为结构化文献卡片（元数据 + 分析摘要），支持 Markdown 或 JSON。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    from yuxi.services.research_deliverable_service import export_paper_cards

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(
        export_paper_cards(
            kb_id=context["kb_id"],
            current_user=user,
            paper_ids=paper_ids or None,
            export_format=format,
        )
    )
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "filename": filename,
        "format": format,
        "media_type": media_type,
        "paper_count": len(paper_ids) if paper_ids else None,
        "artifact_path": artifact_path,
        "message": f"文献卡片已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "分析", "导出"],
    display_name="导出论文分析报告",
    args_schema=ExportPaperAnalysisInput,
)
async def research_export_paper_analysis(run_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """把一次论文分析运行导出为 Markdown 报告，含结构化提取、创新点、方法论与空白。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    from yuxi.services.research_deliverable_service import export_paper_analysis_markdown

    user = await _current_user(runtime)
    filename, content, media_type = await _call(export_paper_analysis_markdown(run_id=run_id, current_user=user))
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "run_id": run_id,
        "filename": filename,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": f"分析报告已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "阅读", "导出"],
    display_name="生成论文分层摘要",
    args_schema=PaperBriefInput,
)
async def research_generate_paper_brief(
    paper_id: str,
    model_spec: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """生成单篇论文的分层摘要（一句话 / 一段 / 一页）并写入 Markdown 交付物。"""
    from yuxi.services.research_deliverable_service import (
        generate_paper_brief,
        render_brief_markdown,
    )

    context = _research_context(runtime)
    user = await _current_user(runtime)
    brief = await _call(
        generate_paper_brief(
            kb_id=context["kb_id"],
            paper_id=paper_id,
            current_user=user,
            model_spec=model_spec,
        )
    )
    filename = f"research_paper_brief_{paper_id}.md"
    artifact_path = await _write_artifact(runtime, filename, render_brief_markdown(brief).encode("utf-8"))
    return {
        **brief,
        "filename": filename,
        "artifact_path": artifact_path,
        "message": f"分层摘要已写入 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "汇报", "导出"],
    display_name="生成汇报包",
    args_schema=PresentationPackInput,
)
async def research_generate_presentation_pack(
    paper_ids: list[str],
    format: str,
    model_spec: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """基于 1 到 10 篇论文生成汇报包（大纲、对比表、讲稿要点），输出 Markdown 或单页 HTML。"""
    from yuxi.services.research_deliverable_service import (
        export_pack,
        generate_presentation_pack,
    )

    context = _research_context(runtime)
    user = await _current_user(runtime)
    pack = await _call(
        generate_presentation_pack(
            kb_id=context["kb_id"],
            paper_ids=paper_ids,
            current_user=user,
            model_spec=model_spec,
        )
    )
    filename, content, media_type = await _call(export_pack(pack=pack, export_format=format))
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "title": pack["title"],
        "section_count": len(pack["outline"]),
        "paper_count": len(pack["papers"]),
        "filename": filename,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": f"汇报包已生成 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "图谱", "导出"],
    display_name="导出引用图谱",
    args_schema=ExportAcademicGraphInput,
)
async def research_export_academic_graph(
    format: str,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把当前知识库的引用图谱快照导出为 CSV 或 GraphML，包含节点、边与最近一次同步运行标识。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    from yuxi.services.academic_graph_sync_service import export_academic_graph

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(
        export_academic_graph(kb_id=context["kb_id"], current_user=user, export_format=format, limit=limit)
    )
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "filename": filename,
        "format": format,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": f"引用图谱已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "结构图", "导出"],
    display_name="生成论文结构图",
    args_schema=StructureDiagramInput,
)
async def research_export_paper_diagram(
    paper_id: str,
    kind: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """根据论文分析结果或关键词生成 SVG 结构图（方法流程 / 主题图），并写入交付物目录。

    导出完成后应调用 present_artifacts 工具将文件展示给用户。
    """
    from yuxi.services.research_deliverable_service import generate_structure_diagram

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(
        generate_structure_diagram(kb_id=context["kb_id"], paper_id=paper_id, current_user=user, kind=kind)
    )
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "paper_id": paper_id,
        "kind": kind,
        "filename": filename,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": f"结构图已生成 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "导出", "数据"],
    display_name="导出科研数据",
    args_schema=ExportResearchDataInput,
)
async def research_export_research_data(
    kind: str,
    format: str,
    target_id: str | None,
    year_from: int | None,
    year_to: int | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """导出评估运行、消融实验、趋势或机会统计为 JSON/CSV，附带数据指纹。"""
    from yuxi.services.research_deliverable_service import export_research_data

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(
        export_research_data(
            kb_id=context["kb_id"],
            current_user=user,
            kind=kind,
            target_id=target_id,
            export_format=format,
            year_from=year_from,
            year_to=year_to,
        )
    )
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "kind": kind,
        "filename": filename,
        "format": format,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": f"数据已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "对比", "导出"],
    display_name="导出文献对比矩阵",
    args_schema=ExportComparisonMatrixInput,
)
async def research_export_comparison_matrix(
    paper_ids: list[str],
    format: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把 2 到 20 篇论文按统一字段渲染为对比矩阵（Markdown / CSV），缺少分析的单元格标注“证据不足”。"""
    from yuxi.services.research_deliverable_service import export_comparison_matrix

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(
        export_comparison_matrix(kb_id=context["kb_id"], current_user=user, paper_ids=paper_ids, export_format=format)
    )
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "filename": filename,
        "format": format,
        "media_type": media_type,
        "paper_count": len(paper_ids),
        "artifact_path": artifact_path,
        "message": f"对比矩阵已导出为 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "图谱", "洞察"],
    display_name="生成引用网络洞察",
    args_schema=CitationInsightInput,
)
async def research_generate_citation_insight(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """基于当前引用图谱快照生成枢纽论文、研究谱系与孤立节点的 Markdown 洞察报告。"""
    del dummy
    from yuxi.services.research_deliverable_service import generate_citation_insight

    context = _research_context(runtime)
    user = await _current_user(runtime)
    insight = await _call(generate_citation_insight(kb_id=context["kb_id"], current_user=user))
    filename = f"research_citation_insight_{context['kb_id']}.md"
    artifact_path = await _write_artifact(runtime, filename, insight["markdown"].encode("utf-8"))
    return {
        "paper_count": insight["paper_count"],
        "citation_edge_count": insight["citation_edge_count"],
        "hub_count": insight["hub_count"],
        "isolate_count": insight["isolate_count"],
        "filename": filename,
        "artifact_path": artifact_path,
        "message": f"洞察报告已写入 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "综述", "选题"],
    display_name="综述转选题清单",
    args_schema=SynthesisTopicsInput,
)
async def research_export_synthesis_topics(run_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """把已验证综述的研究空白、后续方向与未支持结论整理为选题清单（Markdown）。"""
    from yuxi.services.research_deliverable_service import build_topic_list

    user = await _current_user(runtime)
    topics = await _call(build_topic_list(run_id=run_id, current_user=user))
    filename = f"research_topics_{run_id}.md"
    artifact_path = await _write_artifact(runtime, filename, topics["markdown"].encode("utf-8"))
    return {
        "run_id": run_id,
        "topic_count": len(topics["items"]),
        "filename": filename,
        "artifact_path": artifact_path,
        "message": f"选题清单已写入 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "综述", "选题", "项目"],
    display_name="选题转为项目任务",
    args_schema=SynthesisTopicTasksInput,
)
async def research_create_tasks_from_topics(
    run_id: str,
    limit: int,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把综述选题批量建为当前研究项目的待办任务（需当前会话已绑定项目）。"""
    from yuxi.services.research_deliverable_service import create_tasks_from_topics

    user = await _current_user(runtime)
    project_id = await _project_id(runtime, None, user)
    return await _call(create_tasks_from_topics(run_id=run_id, project_id=project_id, current_user=user, limit=limit))


@tool(
    category="research",
    tags=["科研", "去重"],
    display_name="检测重复论文",
    args_schema=EmptyInput,
)
async def research_find_duplicate_papers(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """在当前知识库内按 DOI（优先）或归一化标题检测重复论文，只读不改数据。"""
    del dummy
    from yuxi.services.research_deliverable_service import find_duplicate_papers

    context = _research_context(runtime)
    user = await _current_user(runtime)
    result = await _call(find_duplicate_papers(kb_id=context["kb_id"], current_user=user))
    return {
        **result,
        "groups": result["groups"][:20],
        "message": "仅报告重复，不自动合并；合并策略需人工确认",
    }


@tool(
    category="research",
    tags=["科研", "去重", "跨库"],
    display_name="跨库查重",
    args_schema=EmptyInput,
)
async def research_find_cross_library_duplicates(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """在当前用户可访问的多个知识库之间识别重复论文（只读，不合并）。"""
    del dummy
    from yuxi.services.research_deliverable_service import find_cross_library_duplicates

    user = await _current_user(runtime)
    result = await _call(find_cross_library_duplicates(current_user=user))
    return {**result, "groups": result["groups"][:20]}


@tool(
    category="research",
    tags=["科研", "导出", "迁移"],
    display_name="导出知识库清单",
    args_schema=EmptyInput,
)
async def research_export_kb_manifest(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """导出当前知识库的迁移清单（论文元数据 + 图谱统计），不含原文与向量。"""
    del dummy
    import json

    from yuxi.services.research_deliverable_service import export_kb_manifest

    context = _research_context(runtime)
    user = await _current_user(runtime)
    manifest = await _call(export_kb_manifest(kb_id=context["kb_id"], current_user=user))
    filename = f"research_manifest_{context['kb_id']}.json"
    artifact_path = await _write_artifact(
        runtime, filename, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    )
    return {
        "kb_id": context["kb_id"],
        "paper_count": manifest["paper_count"],
        "filename": filename,
        "artifact_path": artifact_path,
        "message": f"知识库清单已写入 {filename}，请调用 present_artifacts 工具展示该文件",
    }


@tool(
    category="research",
    tags=["科研", "迁移", "导出"],
    display_name="导出知识库迁移包",
    args_schema=EmptyInput,
)
async def research_export_kb_bundle(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """把迁移清单、论文表、文件清单与图谱快照打包为 ZIP（不含原文与向量）。"""
    del dummy
    from yuxi.services.research_deliverable_service import export_kb_bundle

    context = _research_context(runtime)
    user = await _current_user(runtime)
    filename, content, media_type = await _call(export_kb_bundle(kb_id=context["kb_id"], current_user=user))
    artifact_path = await _write_artifact(runtime, filename, content)
    return {
        "kb_id": context["kb_id"],
        "filename": filename,
        "media_type": media_type,
        "artifact_path": artifact_path,
        "message": (
            f"迁移包已写入 {filename}，请调用 present_artifacts 展示；包内不含 PDF 原文与向量，"
            "恢复后请用 research_validate_kb_manifest 核对"
        ),
    }


@tool(
    category="research",
    tags=["科研", "迁移", "校验"],
    display_name="校验迁移清单",
    args_schema=ValidateKbManifestInput,
)
async def research_validate_kb_manifest(manifest_json: str, runtime: ToolRuntime) -> dict[str, Any]:
    """把迁移清单 JSON 与当前库论文比对，报告缺失与新增条目（只读，不修改数据）。"""
    import json

    from yuxi.services.research_deliverable_service import validate_kb_manifest

    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError as exc:
        raise ToolException("迁移清单不是合法 JSON") from exc
    if not isinstance(manifest, dict):
        raise ToolException("迁移清单必须是 JSON 对象")
    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(validate_kb_manifest(kb_id=context["kb_id"], manifest=manifest, current_user=user))


@tool(
    category="research",
    tags=["科研", "评估", "基准"],
    display_name="生成评估基准",
    args_schema=GenerateEvalDatasetInput,
)
async def research_generate_eval_dataset(
    name: str,
    count: int,
    llm_model_spec: str | None,
    description: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """基于当前知识库自动生成 RAG 评估基准（仅管理员），返回基准 ID 与题量。"""
    from yuxi.knowledge.eval.service import EvaluationService
    from yuxi.services.research_deliverable_service import _resolve_model_spec

    context = _research_context(runtime)
    user = await _current_user(runtime)
    await _require_admin(user)
    model_spec = _resolve_model_spec(llm_model_spec)
    created_by = str(getattr(user, "id", "") or getattr(user, "user_id", "") or "system")
    result = await _call(
        EvaluationService().generate_dataset(
            kb_id=context["kb_id"],
            name=name,
            description=description,
            count=count,
            neighbors_count=3,
            concurrency_count=2,
            llm_model_spec=model_spec,
            created_by=created_by,
        )
    )
    return {
        "dataset_id": result.get("dataset_id"),
        "name": result.get("name") or name,
        "question_count": result.get("item_count") or result.get("question_count") or count,
        "message": "评估基准已生成，可在 RAG 评估中选择该基准发起评估",
    }


@tool(
    category="research",
    tags=["科研", "去重", "整理"],
    display_name="合并重复论文（归档式）",
    args_schema=DuplicateMergeInput,
)
async def research_merge_duplicate_group(key: str, runtime: ToolRuntime) -> dict[str, Any]:
    """按重复分组归档除保留条目以外的论文（不删文件，可恢复）。需先调用查重拿分组 key。"""
    from yuxi.services.research_deliverable_service import merge_duplicate_group

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(merge_duplicate_group(kb_id=context["kb_id"], key=key, current_user=user))


@tool(
    category="research",
    tags=["科研", "去重", "跨库"],
    display_name="跨库重复合并",
    args_schema=CrossLibraryMergeInput,
)
async def research_merge_cross_library_duplicate(
    paper_id: str, source_kb_id: str, source_paper_id: str, runtime: ToolRuntime
) -> dict[str, Any]:
    """用对方库同一论文的空字段补齐当前库记录，并把对方库的重复记录归档（不搬原文与资产，可恢复）。"""
    from yuxi.services.research_deliverable_service import merge_cross_library_duplicate

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(
        merge_cross_library_duplicate(
            kb_id=context["kb_id"],
            paper_id=paper_id,
            source_kb_id=source_kb_id,
            source_paper_id=source_paper_id,
            current_user=user,
        )
    )


@tool(
    category="research",
    tags=["科研", "去重", "恢复"],
    display_name="恢复归档论文",
    args_schema=DuplicateRestoreInput,
)
async def research_restore_archived_papers(paper_ids: list[str], runtime: ToolRuntime) -> dict[str, Any]:
    """把已归档的论文恢复为正常状态，重新参与检索与导出。"""
    from yuxi.services.research_deliverable_service import restore_archived_papers

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(restore_archived_papers(kb_id=context["kb_id"], paper_ids=paper_ids, current_user=user))


@tool(
    category="research",
    tags=["科研", "去重", "归档"],
    display_name="查询已归档论文",
    args_schema=EmptyInput,
)
async def research_list_archived_papers(dummy: str, runtime: ToolRuntime) -> dict[str, Any]:
    """列出当前知识库中已归档（合并去重后隐藏）的论文，供核对与恢复。"""
    del dummy
    from yuxi.services.research_deliverable_service import list_archived_papers

    context = _research_context(runtime)
    user = await _current_user(runtime)
    return await _call(list_archived_papers(kb_id=context["kb_id"], current_user=user))


@tool(
    category="research",
    tags=["科研", "论文", "元数据"],
    display_name="补全论文元数据",
    args_schema=EnrichPaperMetadataInput,
)
async def research_enrich_paper_metadata(paper_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """按 DOI 或标题从 Crossref / Semantic Scholar 补全论文缺失的元数据字段（仅管理员，不覆盖已有值）。"""
    from yuxi.services.research_paper_metadata_service import enrich_paper_metadata

    context = _research_context(runtime)
    user = await _current_user(runtime)
    result = await _call(enrich_paper_metadata(kb_id=context["kb_id"], paper_id=paper_id, current_user=user))
    return {
        "paper_id": result["paper_id"],
        "filled": result["filled"],
        "field_sources": result["field_sources"],
        "missing_fields": result["missing_fields"],
        "message": result["message"],
    }


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
    research_run_paper_analysis,
    research_get_paper_analysis,
    research_ask_paper_question,
    research_translate_paper,
    research_list_paper_notes,
    research_create_paper_note,
    research_update_paper_note,
    research_delete_paper_note,
    research_export_paper_notes,
    research_list_related_papers,
    research_import_related_papers,
    research_export_project_report,
    research_export_papers,
    research_import_reference_file,
    research_get_paper_screening,
    research_set_paper_screening,
    research_export_paper_screening,
    research_list_evidence,
    research_create_evidence,
    research_verify_evidence,
    research_get_evidence_audit,
    research_sync_academic_graph,
    research_get_academic_graph,
    research_get_graph_relations,
    research_list_graph_conflicts,
    research_get_trends,
    research_get_opportunities,
    research_list_project_risks,
    research_create_project_risk,
    research_transition_project_risk,
    research_list_project_risk_activities,
    research_add_task_dependency,
    research_remove_task_dependency,
    research_list_user_studies,
    research_create_user_study,
    research_get_user_study_report,
    research_close_user_study,
    research_export_user_study,
    research_create_analysis_evaluation,
    research_list_analysis_evaluations,
    research_get_analysis_evaluation,
    research_search_external_papers,
    research_import_external_paper,
    research_backfill_paper_fulltext,
    research_list_eval_datasets,
    research_upload_eval_dataset,
    research_create_eval_experiment,
    research_run_eval,
    research_get_eval_run,
    research_list_eval_experiments,
    research_get_eval_experiment,
    research_export_paper_cards,
    research_export_paper_analysis,
    research_generate_paper_brief,
    research_generate_presentation_pack,
    research_export_academic_graph,
    research_export_paper_diagram,
    research_export_research_data,
    research_export_comparison_matrix,
    research_generate_citation_insight,
    research_export_synthesis_topics,
    research_create_tasks_from_topics,
    research_find_duplicate_papers,
    research_find_cross_library_duplicates,
    research_export_kb_manifest,
    research_export_kb_bundle,
    research_validate_kb_manifest,
    research_generate_eval_dataset,
    research_merge_duplicate_group,
    research_merge_cross_library_duplicate,
    research_restore_archived_papers,
    research_list_archived_papers,
    research_enrich_paper_metadata,
]
