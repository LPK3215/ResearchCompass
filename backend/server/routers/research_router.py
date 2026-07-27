from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from server.utils.client_ip import extract_client_ip
from yuxi.services.research_copilot_service import (
    ResearchCopilotError,
    ResearchCopilotScope,
    ensure_research_copilot_thread,
)
from yuxi.services.research_project_service import (
    ResearchProjectError,
    add_project_assets,
    create_research_project,
    delete_research_project,
    get_research_project,
    list_project_asset_candidates,
    list_project_assets,
    list_research_projects,
    remove_project_asset,
    update_project_asset_notes,
    update_research_project,
)
from yuxi.services.research_project_plan_service import (
    create_project_milestone,
    create_project_plan_asset_link,
    create_project_task,
    delete_project_milestone,
    delete_project_plan_asset_link,
    delete_project_task,
    get_research_project_plan,
    reorder_project_milestones,
    reorder_project_tasks,
    update_project_milestone,
    update_project_task,
)
from yuxi.services.research_project_report_service import export_research_project_report
from yuxi.services.research_paper_service import (
    add_paper_tag_view,
    export_papers_bibtex,
    get_paper_evidence_view,
    get_paper_view,
    list_paper_chunks_view,
    list_paper_tags_view,
    list_papers_view,
    list_user_tags_view,
    remove_paper_tag_view,
    update_paper_view,
)
from yuxi.services.research_search_service import (
    DEFAULT_RESEARCH_SEARCH_MODE,
    LOCAL_HYBRID_MODE,
    STRICT_HYBRID_GRAPH_MODE,
    ResearchSearchError,
    delete_search_run,
    get_search_run,
    list_search_runs,
    search_papers,
    set_search_run_pinned,
)
from yuxi.services.research_synthesis_service import (
    ResearchSynthesisError,
    cancel_research_synthesis,
    enqueue_research_synthesis,
    export_research_synthesis,
    get_research_synthesis,
    list_research_syntheses,
    regenerate_research_synthesis,
)
from yuxi.services.academic_trend_service import AcademicTrendError, get_academic_trends
from yuxi.services.academic_paper_analysis_service import (
    AcademicPaperAnalysisError,
    enqueue_paper_analysis,
    get_latest_paper_analysis,
    get_paper_analysis_run,
)
from yuxi.services.academic_graph_sync_service import (
    AcademicGraphSyncError,
    enqueue_academic_graph_sync,
    get_academic_graph_relations,
    get_academic_graph_network,
    get_academic_graph_sync_run,
    list_academic_graph_conflicts,
)
from yuxi.services.academic_paper_import_service import (
    AcademicPaperImportError,
    import_external_paper,
    search_external_papers,
)
from yuxi.services.academic_opportunity_service import AcademicOpportunityError, get_academic_opportunities
from yuxi.services.research_user_study_service import (
    ResearchUserStudyError,
    ResearchUserStudyService,
)
from yuxi.services.academic_paper_analysis_evaluation_service import (
    AcademicPaperAnalysisEvaluationError,
    AcademicPaperAnalysisEvaluationService,
)
from yuxi.storage.postgres.models_business import User

research = APIRouter(prefix="/research", tags=["research"])


class PaperTagRequest(BaseModel):
    tag: str = Field(..., min_length=1, max_length=64)

    @field_validator("tag")
    @classmethod
    def normalize_tag(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标签不能为空")
        return normalized


class CreateResearchProjectRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    research_question: str = Field(..., min_length=1, max_length=8000)
    description: str = Field(default="", max_length=12000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    target_date: date | None = None
    next_action: str = Field(default="", max_length=4000)

    @field_validator("title", "research_question")
    @classmethod
    def normalize_required_project_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized

    @field_validator("description", "next_action")
    @classmethod
    def normalize_optional_project_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags")
    @classmethod
    def normalize_project_tags(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 64 for item in normalized):
            raise ValueError("项目标签不能为空且不能超过 64 个字符")
        return list(dict.fromkeys(normalized))


class UpdateResearchProjectRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    research_question: str | None = Field(default=None, max_length=8000)
    description: str | None = Field(default=None, max_length=12000)
    status: str | None = Field(default=None, pattern=r"^(active|completed|archived)$")
    progress: int | None = Field(default=None, ge=0, le=100)
    next_action: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=20)
    target_date: date | None = None

    @field_validator("title", "research_question", "description", "next_action")
    @classmethod
    def normalize_project_update_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("tags")
    @classmethod
    def normalize_project_update_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 64 for item in normalized):
            raise ValueError("项目标签不能为空且不能超过 64 个字符")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_project_update(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        for field in ("title", "research_question", "status", "progress", "tags"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        if "title" in self.model_fields_set and not self.title:
            raise ValueError("项目标题不能为空")
        if "research_question" in self.model_fields_set and not self.research_question:
            raise ValueError("研究问题不能为空")
        return self


class AddResearchProjectAssetsRequest(BaseModel):
    asset_type: str = Field(
        ...,
        pattern=r"^(paper|search_run|synthesis_run|analysis_run|evaluation_experiment)$",
    )
    reference_ids: list[str] = Field(..., min_length=1, max_length=100)
    notes: str = Field(default="", max_length=4000)

    @field_validator("reference_ids")
    @classmethod
    def normalize_project_reference_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 64 for item in normalized):
            raise ValueError("成果标识无效")
        return list(dict.fromkeys(normalized))

    @field_validator("notes")
    @classmethod
    def normalize_asset_notes(cls, value: str) -> str:
        return value.strip()


class UpdateResearchProjectAssetRequest(BaseModel):
    notes: str = Field(default="", max_length=4000)

    @field_validator("notes")
    @classmethod
    def normalize_project_asset_notes(cls, value: str) -> str:
        return value.strip()


class CreateResearchProjectMilestoneRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=12000)
    status: str = Field(default="planned", pattern=r"^(planned|active|completed)$")
    target_date: date | None = None

    @field_validator("title", "description")
    @classmethod
    def normalize_milestone_text(cls, value: str) -> str:
        return value.strip()


class UpdateResearchProjectMilestoneRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=12000)
    status: str | None = Field(default=None, pattern=r"^(planned|active|completed)$")
    target_date: date | None = None

    @field_validator("title", "description")
    @classmethod
    def normalize_milestone_update_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_milestone_update(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        if "title" in self.model_fields_set and not self.title:
            raise ValueError("里程碑名称不能为空")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("里程碑状态不能为 null")
        return self


class ResearchProjectMilestoneOrderRequest(BaseModel):
    milestone_ids: list[str] = Field(..., min_length=1, max_length=500)

    @field_validator("milestone_ids")
    @classmethod
    def validate_milestone_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("里程碑 ID 不能为空或重复")
        return value


class CreateResearchProjectTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=12000)
    status: str = Field(default="todo", pattern=r"^(todo|in_progress|blocked|done)$")
    priority: str = Field(default="medium", pattern=r"^(low|medium|high)$")
    due_date: date | None = None
    milestone_id: str | None = Field(default=None, max_length=64)

    @field_validator("title", "description")
    @classmethod
    def normalize_task_text(cls, value: str) -> str:
        return value.strip()


class UpdateResearchProjectTaskRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=12000)
    status: str | None = Field(default=None, pattern=r"^(todo|in_progress|blocked|done)$")
    priority: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    due_date: date | None = None
    milestone_id: str | None = Field(default=None, max_length=64)

    @field_validator("title", "description")
    @classmethod
    def normalize_task_update_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_task_update(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        for field in ("title", "status", "priority"):
            if field in self.model_fields_set and not getattr(self, field):
                raise ValueError(f"{field} 不能为空")
        return self


class ResearchProjectTaskOrderRequest(BaseModel):
    milestone_id: str | None = Field(default=None, max_length=64)
    task_ids: list[str] = Field(..., min_length=1, max_length=1000)

    @field_validator("task_ids")
    @classmethod
    def validate_task_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("任务 ID 不能为空或重复")
        return value


class CreateResearchProjectPlanAssetLinkRequest(BaseModel):
    asset_id: str = Field(..., min_length=1, max_length=64)
    milestone_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_target(self):
        if bool(self.milestone_id) == bool(self.task_id):
            raise ValueError("必须且只能指定一个里程碑或任务")
        return self


class PaperMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=2000)
    abstract: str | None = Field(default=None, max_length=200_000)
    authors: list[str] | None = Field(default=None, max_length=100)
    publication_year: int | None = Field(default=None, ge=1500, le=datetime.now(UTC).year + 1)
    venue: str | None = Field(default=None, max_length=512)
    doi: str | None = Field(default=None, max_length=512)
    keywords: list[str] | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    external_ids: dict[str, str] | None = None
    citation_count: int | None = Field(default=None, ge=0)

    @field_validator("venue", "doi", "language")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标题不能为空")
        return normalized

    @field_validator("authors", "keywords")
    @classmethod
    def normalize_text_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value if item.strip()]
        if len(normalized) != len(value):
            raise ValueError("列表项不能为空")
        if any(len(item) > 512 for item in normalized):
            raise ValueError("列表项长度不能超过 512 个字符")
        return normalized

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        return self


class ResearchSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    retrieval_mode: str = Field(
        default=DEFAULT_RESEARCH_SEARCH_MODE,
        pattern=rf"^({LOCAL_HYBRID_MODE}|{STRICT_HYBRID_GRAPH_MODE})$",
    )
    top_k: int = Field(default=10, ge=1, le=50)
    recall_top_k: int = Field(default=50, ge=10, le=200)
    year_from: int | None = Field(default=None, ge=1500)
    year_to: int | None = Field(default=None, ge=1500)
    chat_model: str | None = Field(default=None, max_length=512)
    reranker_model: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_search(self):
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from 不能大于 year_to")
        if self.recall_top_k < self.top_k:
            raise ValueError("recall_top_k 不能小于 top_k")
        return self


class UpdateResearchSearchRunRequest(BaseModel):
    is_pinned: bool


class ResearchSynthesisRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=8, ge=2, le=20)
    recall_top_k: int = Field(default=50, ge=2, le=200)
    year_from: int | None = Field(default=None, ge=1500)
    year_to: int | None = Field(default=None, ge=1500)
    model_spec: str | None = Field(default=None, max_length=512)
    reranker_model: str | None = Field(default=None, max_length=512)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("研究问题不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_synthesis(self):
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from 不能大于 year_to")
        if self.recall_top_k < self.top_k:
            raise ValueError("recall_top_k 不能小于 top_k")
        return self


class AcademicGraphSyncRequest(BaseModel):
    paper_ids: list[str] = Field(default_factory=list, max_length=500)
    citation_limit: int = Field(default=100, ge=0, le=5000)
    reference_limit: int = Field(default=100, ge=0, le=5000)

    @field_validator("paper_ids")
    @classmethod
    def normalize_paper_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("paper_ids 不能包含空值")
        return list(dict.fromkeys(normalized))


class AcademicPaperAnalysisRequest(BaseModel):
    model_spec: str | None = Field(default=None, max_length=512)


class CreatePaperAnalysisEvaluationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    paper_ids: list[str] = Field(..., min_length=2, max_length=20)
    model_spec: str = Field(..., min_length=1, max_length=512)

    @field_validator("name", "model_spec")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized

    @field_validator("paper_ids")
    @classmethod
    def normalize_paper_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("paper_ids 不能包含空值")
        return list(dict.fromkeys(normalized))


class SubmitPaperAnalysisBlindScoreRequest(BaseModel):
    blind_scores: dict[str, Any]
    notes: str = Field(default="", max_length=4000)


class CreateUserStudyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    participant_count: int = Field(default=5, ge=3, le=20)
    consent_text: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("name", "consent_text")
    @classmethod
    def normalize_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class SubmitUserStudyResponseRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=256)
    consent: bool
    research_stage: str = Field(..., pattern=r"^(undergraduate|master|doctoral|other)$")
    research_experience: str = Field(..., pattern=r"^(none|under_1_year|1_to_3_years|over_3_years)$")
    task_scores: dict[str, int]
    sus_scores: dict[str, int]
    overall_rating: int = Field(..., ge=1, le=5)
    recommend_score: int = Field(..., ge=0, le=10)
    feedback: str = Field(default="", max_length=4000)


class PublicUserStudyTokenRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=256)


def _search_http_error(exc: ResearchSearchError) -> HTTPException:
    status = {
        "forbidden": 403,
        "run_active": 409,
        "knowledge_base_not_found": 404,
        "run_not_found": 404,
        "invalid_query": 422,
        "invalid_filter": 422,
        "invalid_retrieval_config": 422,
        "unsupported_knowledge_base": 409,
        "graph_not_ready": 409,
        "citation_graph_seed_missing": 409,
        "citation_graph_seed_invalid": 409,
        "chat_model_unavailable": 409,
        "reranker_unavailable": 409,
        "query_rewrite_invalid": 502,
        "evidence_missing_paper": 502,
        "retrieval_failure": 502,
        "citation_graph_failure": 502,
        "local_research_failure": 502,
        "strict_research_failure": 502,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _synthesis_http_error(exc: ResearchSynthesisError) -> HTTPException:
    status = {
        "forbidden": 403,
        "knowledge_base_not_found": 404,
        "synthesis_run_not_found": 404,
        "synthesis_invalid_query": 422,
        "synthesis_invalid_filter": 422,
        "synthesis_invalid_retrieval_config": 422,
        "synthesis_export_format_invalid": 422,
        "synthesis_active": 409,
        "synthesis_model_unavailable": 409,
        "synthesis_reranker_unavailable": 409,
        "synthesis_insufficient_evidence": 409,
        "synthesis_context_exceeded": 409,
        "synthesis_evidence_changed": 409,
        "synthesis_not_exportable": 409,
        "synthesis_not_cancellable": 409,
        "synthesis_task_missing": 409,
        "synthesis_invalid_json": 502,
        "synthesis_invalid_schema": 502,
        "synthesis_invalid_citation": 502,
        "synthesis_generation_failed": 502,
        "synthesis_retrieval_failed": 502,
        "synthesis_record_failed": 502,
        "synthesis_enqueue_failed": 503,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _graph_http_error(exc: AcademicGraphSyncError) -> HTTPException:
    status = {
        "forbidden": 403,
        "paper_not_found": 404,
        "graph_paper_not_found": 404,
        "sync_run_not_found": 404,
        "task_enqueue_failed": 503,
        "graph_sync_active": 409,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _analysis_http_error(exc: AcademicPaperAnalysisError) -> HTTPException:
    status = {
        "forbidden": 403,
        "paper_not_found": 404,
        "analysis_run_not_found": 404,
        "analysis_model_unavailable": 409,
        "analysis_active": 409,
        "paper_content_missing": 409,
        "analysis_context_exceeded": 409,
        "task_enqueue_failed": 503,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _analysis_evaluation_http_error(exc: AcademicPaperAnalysisEvaluationError) -> HTTPException:
    status = {
        "evaluation_not_found": 404,
        "evaluation_item_not_found": 404,
        "paper_not_found": 404,
        "evaluation_item_not_ready": 409,
        "analysis_model_unavailable": 409,
        "paper_content_missing": 409,
        "task_enqueue_failed": 503,
        "invalid_papers": 422,
        "invalid_score": 422,
        "forbidden": 403,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _trend_http_error(exc: AcademicTrendError) -> HTTPException:
    status = {
        "forbidden": 403,
        "invalid_filter": 422,
        "trend_pagination_unsupported": 422,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _opportunity_http_error(exc: AcademicOpportunityError) -> HTTPException:
    status = {"forbidden": 403, "invalid_filter": 422}.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _paper_import_http_error(exc: AcademicPaperImportError) -> HTTPException:
    status = {
        "forbidden": 403,
        "paper_already_imported": 409,
        "file_already_imported": 409,
        "paper_pdf_unavailable": 409,
        "paper_pdf_too_large": 413,
        "unsupported_knowledge_base": 409,
        "paper_import_enqueue_failed": 503,
        "paper_import_record_failed": 502,
        "invalid_search_query": 422,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _user_study_http_error(exc: ResearchUserStudyError) -> HTTPException:
    status = {
        "study_not_found": 404,
        "invite_not_found": 404,
        "invite_used": 409,
        "study_closed": 409,
        "consent_required": 422,
        "invalid_participant_count": 422,
        "invalid_response": 422,
        "rate_limited": 429,
        "rate_limit_unavailable": 503,
    }.get(exc.error_type, 502)
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return HTTPException(
        status_code=status,
        detail={"error": exc.error_type, "message": exc.message},
        headers=headers,
    )


def _public_request_client_key(request: Request) -> str:
    return extract_client_ip(request)


def _project_http_error(exc: ResearchProjectError) -> HTTPException:
    status = {
        "forbidden": 403,
        "project_not_found": 404,
        "asset_not_found": 404,
        "asset_already_linked": 409,
        "project_archived": 409,
        "project_read_only": 409,
        "project_has_open_tasks": 409,
        "milestone_has_open_tasks": 409,
        "milestone_has_tasks": 409,
        "plan_asset_already_linked": 409,
        "milestone_not_found": 404,
        "task_not_found": 404,
        "plan_asset_link_not_found": 404,
        "plan_asset_target_not_found": 404,
        "invalid_project_status": 422,
        "invalid_asset_type": 422,
        "invalid_milestone_status": 422,
        "invalid_task_status": 422,
        "invalid_task_priority": 422,
        "invalid_milestone_order": 422,
        "invalid_task_order": 422,
        "invalid_plan_asset_target": 422,
        "invalid_report_format": 422,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


def _copilot_http_error(exc: ResearchCopilotError) -> HTTPException:
    status = {
        "forbidden": 403,
        "knowledge_base_not_found": 404,
        "project_not_found": 404,
        "thread_not_found": 404,
        "invalid_copilot_thread": 409,
        "project_scope_mismatch": 409,
        "thread_scope_conflict": 409,
        "thread_update_failed": 409,
        "invalid_research_context": 422,
    }.get(exc.error_type, 502)
    return HTTPException(status_code=status, detail={"error": exc.error_type, "message": exc.message})


@research.post("/copilot/thread")
async def ensure_copilot_thread(
    payload: ResearchCopilotScope,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ensure_research_copilot_thread(
            payload=payload.model_dump(mode="json"),
            current_user=current_user,
            db=db,
        )
    except ResearchCopilotError as exc:
        raise _copilot_http_error(exc) from exc


@research.post("/databases/{kb_id}/projects", status_code=201)
async def create_project(
    kb_id: str,
    payload: CreateResearchProjectRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await create_research_project(
            kb_id=kb_id,
            current_user=current_user,
            **payload.model_dump(),
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.get("/databases/{kb_id}/projects")
async def list_projects(
    kb_id: str,
    status: str | None = Query(default=None, pattern=r"^(active|completed|archived)$"),
    query: str | None = Query(default=None, max_length=500),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
):
    try:
        return await list_research_projects(
            kb_id=kb_id,
            current_user=current_user,
            status=status,
            query=query.strip() if query else None,
            offset=offset,
            limit=limit,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.get("/projects/{project_id}")
async def project_detail(project_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await get_research_project(project_id=project_id, current_user=current_user)
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    payload: UpdateResearchProjectRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await update_research_project(
            project_id=project_id,
            current_user=current_user,
            values=payload.model_dump(exclude_unset=True),
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.delete("/projects/{project_id}", status_code=204)
async def remove_project(project_id: str, current_user: User = Depends(get_required_user)):
    try:
        await delete_research_project(project_id=project_id, current_user=current_user)
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.get("/projects/{project_id}/plan")
async def project_plan(project_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await get_research_project_plan(project_id=project_id, current_user=current_user)
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.post("/projects/{project_id}/milestones", status_code=201)
async def create_milestone(
    project_id: str,
    payload: CreateResearchProjectMilestoneRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await create_project_milestone(
            project_id=project_id,
            current_user=current_user,
            **payload.model_dump(),
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.patch("/projects/{project_id}/milestones/{milestone_id}")
async def update_milestone(
    project_id: str,
    milestone_id: str,
    payload: UpdateResearchProjectMilestoneRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await update_project_milestone(
            project_id=project_id,
            milestone_id=milestone_id,
            current_user=current_user,
            values=payload.model_dump(exclude_unset=True),
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.delete("/projects/{project_id}/milestones/{milestone_id}", status_code=204)
async def remove_milestone(
    project_id: str,
    milestone_id: str,
    current_user: User = Depends(get_required_user),
):
    try:
        await delete_project_milestone(
            project_id=project_id,
            milestone_id=milestone_id,
            current_user=current_user,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.put("/projects/{project_id}/milestones/order", status_code=204)
async def reorder_milestones(
    project_id: str,
    payload: ResearchProjectMilestoneOrderRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        await reorder_project_milestones(
            project_id=project_id,
            current_user=current_user,
            milestone_ids=payload.milestone_ids,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.post("/projects/{project_id}/tasks", status_code=201)
async def create_task(
    project_id: str,
    payload: CreateResearchProjectTaskRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await create_project_task(
            project_id=project_id,
            current_user=current_user,
            **payload.model_dump(),
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.patch("/projects/{project_id}/tasks/{task_id}")
async def update_task(
    project_id: str,
    task_id: str,
    payload: UpdateResearchProjectTaskRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await update_project_task(
            project_id=project_id,
            task_id=task_id,
            current_user=current_user,
            values=payload.model_dump(exclude_unset=True),
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.delete("/projects/{project_id}/tasks/{task_id}", status_code=204)
async def remove_task(
    project_id: str,
    task_id: str,
    current_user: User = Depends(get_required_user),
):
    try:
        await delete_project_task(project_id=project_id, task_id=task_id, current_user=current_user)
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.put("/projects/{project_id}/tasks/order", status_code=204)
async def reorder_tasks(
    project_id: str,
    payload: ResearchProjectTaskOrderRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        await reorder_project_tasks(
            project_id=project_id,
            current_user=current_user,
            milestone_id=payload.milestone_id,
            task_ids=payload.task_ids,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.post("/projects/{project_id}/plan/asset-links", status_code=201)
async def create_plan_asset_link(
    project_id: str,
    payload: CreateResearchProjectPlanAssetLinkRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await create_project_plan_asset_link(
            project_id=project_id,
            current_user=current_user,
            **payload.model_dump(),
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.delete("/projects/{project_id}/plan/asset-links/{link_id}", status_code=204)
async def remove_plan_asset_link(
    project_id: str,
    link_id: str,
    current_user: User = Depends(get_required_user),
):
    try:
        await delete_project_plan_asset_link(
            project_id=project_id,
            link_id=link_id,
            current_user=current_user,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.get("/projects/{project_id}/report")
async def project_report(
    project_id: str,
    format: str = Query(default="markdown", pattern=r"^(markdown|docx)$"),
    current_user: User = Depends(get_required_user),
):
    try:
        filename, content, media_type = await export_research_project_report(
            project_id=project_id,
            current_user=current_user,
            export_format=format,
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.get("/projects/{project_id}/asset-candidates")
async def project_asset_candidates(
    project_id: str,
    asset_type: str = Query(
        ...,
        pattern=r"^(paper|search_run|synthesis_run|analysis_run|evaluation_experiment)$",
    ),
    query: str | None = Query(default=None, max_length=500),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
):
    try:
        return await list_project_asset_candidates(
            project_id=project_id,
            current_user=current_user,
            asset_type=asset_type,
            query=query.strip() if query else None,
            offset=offset,
            limit=limit,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.post("/projects/{project_id}/assets", status_code=201)
async def add_assets_to_project(
    project_id: str,
    payload: AddResearchProjectAssetsRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await add_project_assets(
            project_id=project_id,
            current_user=current_user,
            **payload.model_dump(),
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.get("/projects/{project_id}/assets")
async def project_assets(
    project_id: str,
    asset_type: str | None = Query(
        default=None,
        pattern=r"^(paper|search_run|synthesis_run|analysis_run|evaluation_experiment)$",
    ),
    query: str | None = Query(default=None, max_length=500),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
):
    try:
        return await list_project_assets(
            project_id=project_id,
            current_user=current_user,
            asset_type=asset_type,
            query=query.strip() if query else None,
            offset=offset,
            limit=limit,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.patch("/projects/{project_id}/assets/{asset_id}")
async def update_project_asset(
    project_id: str,
    asset_id: str,
    payload: UpdateResearchProjectAssetRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await update_project_asset_notes(
            project_id=project_id,
            asset_id=asset_id,
            current_user=current_user,
            notes=payload.notes,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.delete("/projects/{project_id}/assets/{asset_id}")
async def remove_asset_from_project(
    project_id: str,
    asset_id: str,
    current_user: User = Depends(get_required_user),
):
    try:
        return await remove_project_asset(
            project_id=project_id,
            asset_id=asset_id,
            current_user=current_user,
        )
    except ResearchProjectError as exc:
        raise _project_http_error(exc) from exc


@research.get("/databases/{kb_id}/papers")
async def list_papers(
    kb_id: str,
    query: str | None = Query(default=None, max_length=500),
    year_from: int | None = Query(default=None, ge=1500),
    year_to: int | None = Query(default=None, ge=1500),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sort_by: str = Query(default="year", pattern=r"^(year|citation_count|title|created_at)$"),
    sort_order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    current_user: User = Depends(get_required_user),
):
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(status_code=422, detail="year_from 不能大于 year_to")
    return await list_papers_view(
        kb_id=kb_id,
        current_user=current_user,
        query=query,
        year_from=year_from,
        year_to=year_to,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@research.post("/databases/{kb_id}/user-studies")
async def create_user_study(
    kb_id: str,
    payload: CreateUserStudyRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await ResearchUserStudyService().create_study(
            kb_id=kb_id,
            current_user=current_user,
            name=payload.name,
            description=payload.description,
            participant_count=payload.participant_count,
            consent_text=payload.consent_text,
        )
    except ResearchUserStudyError as exc:
        raise _user_study_http_error(exc) from exc


@research.get("/databases/{kb_id}/user-studies")
async def list_user_studies(kb_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await ResearchUserStudyService().list_studies(kb_id=kb_id, current_user=current_user)
    except ResearchUserStudyError as exc:
        raise _user_study_http_error(exc) from exc


@research.get("/databases/{kb_id}/user-studies/{study_id}")
async def get_user_study_report(
    kb_id: str,
    study_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    current_user: User = Depends(get_required_user),
):
    try:
        return await ResearchUserStudyService().get_study_report(
            kb_id=kb_id,
            study_id=study_id,
            current_user=current_user,
            page=page,
            page_size=page_size,
        )
    except ResearchUserStudyError as exc:
        raise _user_study_http_error(exc) from exc


@research.post("/databases/{kb_id}/user-studies/{study_id}/close")
async def close_user_study(kb_id: str, study_id: str, current_user: User = Depends(get_required_user)):
    try:
        await ResearchUserStudyService().close_study(kb_id=kb_id, study_id=study_id, current_user=current_user)
        return {"status": "closed"}
    except ResearchUserStudyError as exc:
        raise _user_study_http_error(exc) from exc


@research.get("/databases/{kb_id}/user-studies/{study_id}/export")
async def export_user_study(kb_id: str, study_id: str, current_user: User = Depends(get_required_user)):
    try:
        filename, content = await ResearchUserStudyService().export_study_csv(
            kb_id=kb_id, study_id=study_id, current_user=current_user
        )
        return Response(
            content=content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ResearchUserStudyError as exc:
        raise _user_study_http_error(exc) from exc


@research.post("/user-studies/public/resolve")
async def get_public_user_study(payload: PublicUserStudyTokenRequest, request: Request):
    try:
        return await ResearchUserStudyService().get_public_study(
            payload.token,
            client_key=_public_request_client_key(request),
        )
    except ResearchUserStudyError as exc:
        raise _user_study_http_error(exc) from exc


@research.post("/user-studies/public/responses")
async def submit_public_user_study_response(payload: SubmitUserStudyResponseRequest, request: Request):
    try:
        return await ResearchUserStudyService().submit_public_response(
            token=payload.token,
            consent=payload.consent,
            research_stage=payload.research_stage,
            research_experience=payload.research_experience,
            task_scores=payload.task_scores,
            sus_scores=payload.sus_scores,
            overall_rating=payload.overall_rating,
            recommend_score=payload.recommend_score,
            feedback=payload.feedback,
            client_key=_public_request_client_key(request),
        )
    except ResearchUserStudyError as exc:
        raise _user_study_http_error(exc) from exc


@research.get("/databases/{kb_id}/trends")
async def academic_trends(
    kb_id: str,
    year_from: int | None = Query(default=None, ge=1500),
    year_to: int | None = Query(default=None, ge=1500),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10000, ge=1, le=50000),
    top_keywords: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
):
    try:
        return await get_academic_trends(
            kb_id=kb_id,
            current_user=current_user,
            year_from=year_from,
            year_to=year_to,
            offset=offset,
            limit=limit,
            top_keywords=top_keywords,
        )
    except AcademicTrendError as exc:
        raise _trend_http_error(exc) from exc


@research.get("/databases/{kb_id}/opportunities")
async def academic_opportunities(
    kb_id: str,
    year_from: int | None = Query(default=None, ge=1500),
    year_to: int | None = Query(default=None, ge=1500),
    limit: int = Query(default=10, ge=1, le=20),
    current_user: User = Depends(get_required_user),
):
    try:
        return await get_academic_opportunities(
            kb_id=kb_id,
            current_user=current_user,
            year_from=year_from,
            year_to=year_to,
            limit=limit,
        )
    except AcademicOpportunityError as exc:
        raise _opportunity_http_error(exc) from exc


@research.get("/databases/{kb_id}/external-papers/search")
async def search_external_paper_catalog(
    kb_id: str,
    query: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=20),
    current_user: User = Depends(get_required_user),
):
    try:
        return await search_external_papers(
            kb_id=kb_id,
            current_user=current_user,
            query=query,
            limit=limit,
        )
    except AcademicPaperImportError as exc:
        raise _paper_import_http_error(exc) from exc


class ExternalPaperImportRequest(BaseModel):
    identifier: str = Field(..., min_length=1, max_length=256)


@research.post("/databases/{kb_id}/external-papers/import")
async def import_external_paper_route(
    kb_id: str,
    payload: ExternalPaperImportRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await import_external_paper(
            kb_id=kb_id,
            identifier=payload.identifier,
            current_user=current_user,
        )
    except AcademicPaperImportError as exc:
        raise _paper_import_http_error(exc) from exc


@research.post("/databases/{kb_id}/search")
async def research_search(
    kb_id: str,
    payload: ResearchSearchRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await search_papers(
            kb_id=kb_id,
            current_user=current_user,
            query=payload.query,
            retrieval_mode=payload.retrieval_mode,
            top_k=payload.top_k,
            recall_top_k=payload.recall_top_k,
            year_from=payload.year_from,
            year_to=payload.year_to,
            chat_model=payload.chat_model,
            reranker_model=payload.reranker_model,
        )
    except ResearchSearchError as exc:
        raise _search_http_error(exc) from exc


@research.get("/search-runs/{run_id}")
async def research_search_run(run_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await get_search_run(run_id=run_id, current_user=current_user)
    except ResearchSearchError as exc:
        raise _search_http_error(exc) from exc


@research.get("/databases/{kb_id}/search-runs")
async def research_search_runs(
    kb_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
):
    try:
        return await list_search_runs(
            kb_id=kb_id,
            current_user=current_user,
            offset=offset,
            limit=limit,
        )
    except ResearchSearchError as exc:
        raise _search_http_error(exc) from exc


@research.patch("/search-runs/{run_id}")
async def update_research_search_run(
    run_id: str,
    payload: UpdateResearchSearchRunRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await set_search_run_pinned(
            run_id=run_id,
            current_user=current_user,
            is_pinned=payload.is_pinned,
        )
    except ResearchSearchError as exc:
        raise _search_http_error(exc) from exc


@research.delete("/search-runs/{run_id}", status_code=204)
async def remove_research_search_run(run_id: str, current_user: User = Depends(get_required_user)):
    try:
        await delete_search_run(run_id=run_id, current_user=current_user)
    except ResearchSearchError as exc:
        raise _search_http_error(exc) from exc


@research.post("/databases/{kb_id}/syntheses")
async def create_research_synthesis(
    kb_id: str,
    payload: ResearchSynthesisRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await enqueue_research_synthesis(
            kb_id=kb_id,
            current_user=current_user,
            query=payload.query,
            top_k=payload.top_k,
            recall_top_k=payload.recall_top_k,
            year_from=payload.year_from,
            year_to=payload.year_to,
            model_spec=payload.model_spec,
            reranker_model=payload.reranker_model,
        )
    except ResearchSynthesisError as exc:
        raise _synthesis_http_error(exc) from exc


@research.get("/databases/{kb_id}/syntheses")
async def list_research_synthesis_runs(
    kb_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
):
    try:
        return await list_research_syntheses(
            kb_id=kb_id,
            current_user=current_user,
            offset=offset,
            limit=limit,
        )
    except ResearchSynthesisError as exc:
        raise _synthesis_http_error(exc) from exc


@research.get("/synthesis-runs/{run_id}")
async def research_synthesis_run(run_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await get_research_synthesis(run_id=run_id, current_user=current_user)
    except ResearchSynthesisError as exc:
        raise _synthesis_http_error(exc) from exc


@research.post("/synthesis-runs/{run_id}/cancel")
async def cancel_research_synthesis_run(run_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await cancel_research_synthesis(run_id=run_id, current_user=current_user)
    except ResearchSynthesisError as exc:
        raise _synthesis_http_error(exc) from exc


@research.post("/synthesis-runs/{run_id}/regenerate")
async def regenerate_research_synthesis_run(run_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await regenerate_research_synthesis(run_id=run_id, current_user=current_user)
    except ResearchSynthesisError as exc:
        raise _synthesis_http_error(exc) from exc


@research.get("/synthesis-runs/{run_id}/export")
async def export_research_synthesis_run(
    run_id: str,
    format: str = Query(default="markdown", pattern=r"^(markdown|docx)$"),
    current_user: User = Depends(get_required_user),
):
    try:
        filename, content, media_type = await export_research_synthesis(
            run_id=run_id,
            current_user=current_user,
            export_format=format,
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ResearchSynthesisError as exc:
        raise _synthesis_http_error(exc) from exc


@research.post("/databases/{kb_id}/academic-graph/sync")
async def sync_academic_graph(
    kb_id: str,
    payload: AcademicGraphSyncRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await enqueue_academic_graph_sync(
            kb_id=kb_id,
            current_user=current_user,
            paper_ids=payload.paper_ids,
            citation_limit=payload.citation_limit,
            reference_limit=payload.reference_limit,
        )
    except AcademicGraphSyncError as exc:
        raise _graph_http_error(exc) from exc


@research.get("/academic-graph/sync-runs/{run_id}")
async def academic_graph_sync_run(run_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await get_academic_graph_sync_run(run_id=run_id, current_user=current_user)
    except AcademicGraphSyncError as exc:
        raise _graph_http_error(exc) from exc


@research.get("/academic-graph/sync-runs/{run_id}/conflicts")
async def academic_graph_conflicts(
    run_id: str,
    resolution_status: str | None = Query(default=None, pattern=r"^(unresolved|resolved|dismissed)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_required_user),
):
    try:
        return await list_academic_graph_conflicts(
            run_id=run_id,
            current_user=current_user,
            resolution_status=resolution_status,
            offset=offset,
            limit=limit,
        )
    except AcademicGraphSyncError as exc:
        raise _graph_http_error(exc) from exc


@research.get("/databases/{kb_id}/academic-graph")
async def academic_graph_network(
    kb_id: str,
    center_paper_id: str | None = Query(default=None, max_length=64),
    depth: int = Query(default=1, ge=1, le=3),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: User = Depends(get_required_user),
):
    try:
        return await get_academic_graph_network(
            kb_id=kb_id,
            current_user=current_user,
            center_paper_id=center_paper_id,
            depth=depth,
            limit=limit,
        )
    except AcademicGraphSyncError as exc:
        raise _graph_http_error(exc) from exc


@research.get("/databases/{kb_id}/academic-graph/relations/{graph_paper_id}")
async def academic_graph_relations(
    kb_id: str,
    graph_paper_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
):
    try:
        return await get_academic_graph_relations(
            kb_id=kb_id,
            current_user=current_user,
            graph_paper_id=graph_paper_id,
            limit=limit,
        )
    except AcademicGraphSyncError as exc:
        raise _graph_http_error(exc) from exc


@research.get("/databases/{kb_id}/papers/export")
async def export_papers(
    kb_id: str,
    paper_ids: str | None = Query(default=None, max_length=4000),
    current_user: User = Depends(get_required_user),
):
    ids = [pid.strip() for pid in paper_ids.split(",") if pid.strip()] if paper_ids else None
    content = await export_papers_bibtex(kb_id=kb_id, current_user=current_user, paper_ids=ids)
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="papers_{kb_id}.bib"'},
    )


@research.get("/databases/{kb_id}/papers/tags")
async def list_user_tags(kb_id: str, current_user: User = Depends(get_required_user)):
    return await list_user_tags_view(kb_id=kb_id, current_user=current_user)


@research.get("/databases/{kb_id}/papers/{paper_id}")
async def get_paper(kb_id: str, paper_id: str, current_user: User = Depends(get_required_user)):
    return await get_paper_view(kb_id=kb_id, paper_id=paper_id, current_user=current_user)


@research.get("/databases/{kb_id}/papers/{paper_id}/chunks")
async def list_paper_chunks(
    kb_id: str,
    paper_id: str,
    section_type: str | None = Query(default=None, max_length=64),
    chunk_id: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_required_user),
):
    return await list_paper_chunks_view(
        kb_id=kb_id,
        paper_id=paper_id,
        current_user=current_user,
        section_type=section_type,
        chunk_id=chunk_id,
        offset=offset,
        limit=limit,
    )


@research.get("/databases/{kb_id}/papers/{paper_id}/evidence/{chunk_id}")
async def get_paper_evidence(kb_id: str, paper_id: str, chunk_id: str, current_user: User = Depends(get_required_user)):
    return await get_paper_evidence_view(
        kb_id=kb_id,
        paper_id=paper_id,
        chunk_id=chunk_id,
        current_user=current_user,
    )


@research.post("/databases/{kb_id}/papers/{paper_id}/analysis")
async def analyze_paper(
    kb_id: str,
    paper_id: str,
    payload: AcademicPaperAnalysisRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await enqueue_paper_analysis(
            kb_id=kb_id,
            paper_id=paper_id,
            current_user=current_user,
            model_spec=payload.model_spec,
        )
    except AcademicPaperAnalysisError as exc:
        raise _analysis_http_error(exc) from exc


@research.get("/paper-analysis-runs/{run_id}")
async def paper_analysis_run(run_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await get_paper_analysis_run(run_id=run_id, current_user=current_user)
    except AcademicPaperAnalysisError as exc:
        raise _analysis_http_error(exc) from exc


@research.get("/databases/{kb_id}/papers/{paper_id}/analysis/latest")
async def latest_paper_analysis(kb_id: str, paper_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await get_latest_paper_analysis(kb_id=kb_id, paper_id=paper_id, current_user=current_user)
    except AcademicPaperAnalysisError as exc:
        raise _analysis_http_error(exc) from exc


@research.post("/databases/{kb_id}/paper-analysis-evaluations")
async def create_paper_analysis_evaluation(
    kb_id: str,
    payload: CreatePaperAnalysisEvaluationRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await AcademicPaperAnalysisEvaluationService().create_evaluation(
            kb_id=kb_id,
            current_user=current_user,
            name=payload.name,
            description=payload.description,
            paper_ids=payload.paper_ids,
            model_spec=payload.model_spec,
        )
    except AcademicPaperAnalysisEvaluationError as exc:
        raise _analysis_evaluation_http_error(exc) from exc


@research.get("/databases/{kb_id}/paper-analysis-evaluations")
async def list_paper_analysis_evaluations(kb_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await AcademicPaperAnalysisEvaluationService().list_evaluations(kb_id=kb_id, current_user=current_user)
    except AcademicPaperAnalysisEvaluationError as exc:
        raise _analysis_evaluation_http_error(exc) from exc


@research.get("/databases/{kb_id}/paper-analysis-evaluations/{evaluation_id}")
async def get_paper_analysis_evaluation_report(
    kb_id: str, evaluation_id: str, current_user: User = Depends(get_required_user)
):
    try:
        return await AcademicPaperAnalysisEvaluationService().get_report(
            kb_id=kb_id, evaluation_id=evaluation_id, current_user=current_user
        )
    except AcademicPaperAnalysisEvaluationError as exc:
        raise _analysis_evaluation_http_error(exc) from exc


@research.get("/databases/{kb_id}/paper-analysis-evaluations/{evaluation_id}/blind-items")
async def list_paper_analysis_blind_items(
    kb_id: str, evaluation_id: str, current_user: User = Depends(get_required_user)
):
    try:
        return await AcademicPaperAnalysisEvaluationService().list_blind_items(
            kb_id=kb_id, evaluation_id=evaluation_id, current_user=current_user
        )
    except AcademicPaperAnalysisEvaluationError as exc:
        raise _analysis_evaluation_http_error(exc) from exc


@research.get("/databases/{kb_id}/paper-analysis-evaluations/{evaluation_id}/blind-items/{item_id}")
async def get_paper_analysis_blind_item(
    kb_id: str, evaluation_id: str, item_id: str, current_user: User = Depends(get_required_user)
):
    try:
        return await AcademicPaperAnalysisEvaluationService().get_blind_item(
            kb_id=kb_id, evaluation_id=evaluation_id, item_id=item_id, current_user=current_user
        )
    except AcademicPaperAnalysisEvaluationError as exc:
        raise _analysis_evaluation_http_error(exc) from exc


@research.post("/databases/{kb_id}/paper-analysis-evaluations/{evaluation_id}/blind-items/{item_id}/scores")
async def submit_paper_analysis_blind_score(
    kb_id: str,
    evaluation_id: str,
    item_id: str,
    payload: SubmitPaperAnalysisBlindScoreRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        return await AcademicPaperAnalysisEvaluationService().submit_blind_score(
            kb_id=kb_id,
            evaluation_id=evaluation_id,
            item_id=item_id,
            current_user=current_user,
            blind_scores=payload.blind_scores,
            notes=payload.notes,
        )
    except AcademicPaperAnalysisEvaluationError as exc:
        raise _analysis_evaluation_http_error(exc) from exc


@research.get("/databases/{kb_id}/papers/{paper_id}/tags")
async def list_paper_tags(kb_id: str, paper_id: str, current_user: User = Depends(get_required_user)):
    return await list_paper_tags_view(kb_id=kb_id, paper_id=paper_id, current_user=current_user)


@research.post("/databases/{kb_id}/papers/{paper_id}/tags")
async def add_paper_tag(
    kb_id: str,
    paper_id: str,
    payload: PaperTagRequest,
    current_user: User = Depends(get_required_user),
):
    return await add_paper_tag_view(kb_id=kb_id, paper_id=paper_id, tag=payload.tag, current_user=current_user)


@research.delete("/databases/{kb_id}/papers/{paper_id}/tags")
async def remove_paper_tag(
    kb_id: str,
    paper_id: str,
    tag: str = Query(..., min_length=1, max_length=64),
    current_user: User = Depends(get_required_user),
):
    return await remove_paper_tag_view(kb_id=kb_id, paper_id=paper_id, tag=tag, current_user=current_user)


@research.patch("/databases/{kb_id}/papers/{paper_id}")
async def update_paper(
    kb_id: str,
    paper_id: str,
    payload: PaperMetadataUpdate,
    current_user: User = Depends(get_required_user),
):
    return await update_paper_view(
        kb_id=kb_id,
        paper_id=paper_id,
        current_user=current_user,
        updates=payload.model_dump(exclude_unset=True),
    )
