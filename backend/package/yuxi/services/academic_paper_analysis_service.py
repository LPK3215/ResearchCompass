from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from yuxi.config import config
from yuxi.models import select_model
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.academic_graph_repository import AcademicGraphRepository
from yuxi.repositories.academic_paper_analysis_repository import AcademicPaperAnalysisRepository
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.services.research_paper_service import _ensure_access, _serialize_paper
from yuxi.services.academic_paper_analysis_workflow import build_analysis_workflow
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.postgres.models_business import User


class AcademicPaperAnalysisError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


ANALYSIS_STRATEGIES = {"single_agent", "multi_agent"}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_json(content: str, *, stage: str) -> dict[str, Any]:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise AcademicPaperAnalysisError("analysis_invalid_json", f"{stage} 阶段返回的 JSON 不可解析") from exc
    if not isinstance(payload, dict):
        raise AcademicPaperAnalysisError("analysis_invalid_schema", f"{stage} 阶段必须返回 JSON 对象")
    return payload


def _validate_stage(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "structure": {"title", "authors", "problem", "method", "datasets", "results", "limitations"},
        "innovations": {"items"},
        "methodology": {"research_design", "method_steps", "evaluation", "reproducibility"},
        "gaps": {"evidence", "gaps", "future_directions"},
    }[stage]
    missing = sorted(key for key in required if key not in payload)
    if missing:
        raise AcademicPaperAnalysisError("analysis_invalid_schema", f"{stage} 阶段缺少字段: {', '.join(missing)}")
    if stage == "innovations" and (
        not isinstance(payload["items"], list) or not 1 <= len(payload["items"]) <= 10
    ):
        raise AcademicPaperAnalysisError("analysis_invalid_schema", "innovations.items 必须是 1 到 10 项数组")
    if stage == "gaps" and not isinstance(payload["gaps"], list):
        raise AcademicPaperAnalysisError("analysis_invalid_schema", "gaps.gaps 必须是数组")
    return payload


STAGE_INSTRUCTIONS = {
    "structure": (
        "你是资深科研论文结构化分析专家。从论文正文提取可核验的元信息与核心主张，"
        "不要补充外部知识。输出 JSON："
        '{"title":"...","authors":[],"problem":"...","method":"...",'
        '"datasets":[],"results":[],"limitations":[]}。'
        "datasets/results/limitations 使用简短字符串数组；无法确定时用空数组，不要猜测。"
    ),
    "innovations": (
        "你是论文创新点审稿人。只保留正文明确支持的 3-5 个核心创新点，"
        "每项必须附原文证据短语。输出 JSON："
        '{"items":[{"claim":"...","evidence":"...","confidence":"high|medium|low"}]}。'
        "confidence=high 仅当证据直接对应主张；证据不足不要编造。"
    ),
    "methodology": (
        "你是方法论与可复现性分析专家。梳理研究设计、关键步骤、评估协议与复现条件。"
        "输出 JSON："
        '{"research_design":"...","method_steps":[],"evaluation":[],'
        '"reproducibility":{"available":true,"details":[]}}。'
        "reproducibility.available 仅在论文提供代码/数据/超参等可复现线索时为 true。"
    ),
    "gaps": (
        "你是科研空白发现专家。必须结合论文正文、前序阶段结论以及引用图谱邻域证据，"
        "识别被明确指出或可由证据支撑的研究空白与后续方向。"
        "输出 JSON："
        '{"evidence":[],"gaps":[{"claim":"...","basis":"...","confidence":"high|medium|low"}],'
        '"future_directions":[]}。'
        "evidence 列出支撑空白判断的原文或图谱关联摘要；"
        "若图谱邻域与正文冲突，以正文为准并在 basis 中说明。"
        "禁止编造未出现的引用关系或实验结果。"
    ),
}


async def _call_stage(model_spec: str, stage: str, context: str) -> dict[str, Any]:
    model = select_model(model_spec=model_spec, model_params={"temperature": 0})
    response = await model.call(
        [
            {
                "role": "system",
                "content": (
                    "只返回合法 JSON 对象，不要 Markdown 代码块，不要额外解释。"
                    f"当前阶段是 {stage}。{STAGE_INSTRUCTIONS[stage]}"
                ),
            },
            {"role": "user", "content": context},
        ],
        stream=False,
    )
    return _validate_stage(stage, _parse_json(str(response.content or ""), stage=stage))


async def _call_single_agent(model_spec: str, context: str) -> dict[str, Any]:
    model = select_model(model_spec=model_spec, model_params={"temperature": 0})
    response = await model.call(
        [
            {
                "role": "system",
                "content": (
                    "你是资深科研论文分析专家。请在一次完整分析中返回合法 JSON，不要 Markdown，"
                    "不要编造论文没有提供的事实；无法判断时明确说明证据不足。输出必须严格符合："
                    '{"structure":{"title":"...","authors":[],"problem":"...","method":"...",'
                    '"datasets":[],"results":[],"limitations":[]},'
                    '"innovations":{"items":[{"claim":"...","evidence":"...",'
                    '"confidence":"high|medium|low"}]},'
                    '"methodology":{"research_design":"...","method_steps":[],"evaluation":[],'
                    '"reproducibility":{"available":true,"details":[]}},'
                    '"research_gaps":{"evidence":[],"gaps":[{"claim":"...","basis":"...",'
                    '"confidence":"high|medium|low"}],"future_directions":[]}}。'
                    "只保留论文明确支持的内容；无法判断时明确说明证据不足。"
                ),
            },
            {"role": "user", "content": context},
        ],
        stream=False,
    )
    payload = _parse_json(str(response.content or ""), stage="single_agent")
    required = {"structure", "innovations", "methodology", "research_gaps"}
    missing = sorted(required - set(payload))
    if missing:
        raise AcademicPaperAnalysisError("analysis_invalid_schema", f"single_agent 阶段缺少字段: {', '.join(missing)}")
    return {
        "structure": _validate_stage("structure", payload["structure"]),
        "innovations": _validate_stage("innovations", payload["innovations"]),
        "methodology": _validate_stage("methodology", payload["methodology"]),
        "research_gaps": _validate_stage("gaps", payload["research_gaps"]),
    }


async def _paper_context(kb_id: str, paper_id: str) -> tuple[Any, str]:
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise AcademicPaperAnalysisError("paper_not_found", "论文不存在")
    chunks, _ = await KnowledgeChunkRepository().list_academic_by_file_id(
        file_id=paper.file_id, offset=0, limit=500
    )
    text = "\n\n".join(
        (
            f"[{chunk.chunk_metadata.get('section_title') or chunk.chunk_metadata.get('section_type') or '内容'}]"
            f"\n{chunk.content}"
        )
        for chunk in chunks
    )
    if not text.strip():
        raise AcademicPaperAnalysisError("paper_content_missing", "论文没有可分析的学术分块")
    return paper, f"论文元数据:\n{json.dumps(_serialize_paper(paper), ensure_ascii=False)}\n\n论文内容:\n{text}"


def _serialize_graph_neighbor(paper) -> dict[str, Any]:
    return {
        "graph_paper_id": paper.graph_paper_id,
        "title": paper.title,
        "publication_year": paper.publication_year,
        "venue": paper.venue,
        "citation_count": paper.citation_count,
        "is_library_paper": bool(paper.is_library_paper),
        "abstract": (paper.abstract or "")[:500] or None,
    }


async def _citation_graph_context(kb_id: str, paper) -> str:
    """构建多 Agent 研究空白阶段所需的引用图谱邻域上下文。

    严格模式：论文必须已同步到学术引用图谱，否则显式失败，不静默降级。
    """
    graph_repo = AcademicGraphRepository()
    graph_paper = await graph_repo.get_graph_paper_by_library_paper(
        kb_id=kb_id,
        academic_paper_id=int(paper.id),
    )
    if graph_paper is None:
        raise AcademicPaperAnalysisError(
            "citation_graph_missing",
            "论文尚未同步到学术引用图谱，无法执行图谱增强的多 Agent 研究空白分析。请先完成引用图谱同步。",
        )

    neighbors = await graph_repo.list_neighbor_citations(
        kb_id=kb_id,
        graph_paper_id=str(graph_paper.graph_paper_id),
        limit=40,
    )
    if not neighbors["citations"]:
        raise AcademicPaperAnalysisError(
            "citation_graph_empty",
            "论文已在引用图谱中，但尚未建立可用的 CITES 邻域关系，无法执行研究空白图谱推理。",
        )

    two_hop = await graph_repo.find_two_hop_relations(
        kb_id=kb_id,
        source_graph_paper_id=str(graph_paper.graph_paper_id),
        limit=20,
    )
    papers = neighbors["papers"]
    outgoing = []
    incoming = []
    for edge in neighbors["citations"]:
        if edge.citing_paper_id == graph_paper.graph_paper_id:
            target = papers.get(edge.cited_paper_id)
            if target is not None:
                outgoing.append(_serialize_graph_neighbor(target))
        elif edge.cited_paper_id == graph_paper.graph_paper_id:
            source = papers.get(edge.citing_paper_id)
            if source is not None:
                incoming.append(_serialize_graph_neighbor(source))

    relation_summaries = []
    if two_hop is not None:
        hop_papers = two_hop["papers"]
        for path in two_hop["paths"][:20]:
            target = hop_papers.get(path["target_graph_paper_id"])
            middle = hop_papers.get(path["intermediate_graph_paper_id"])
            relation_summaries.append(
                {
                    "relation_type": path["relation_type"],
                    "target_title": getattr(target, "title", None),
                    "intermediate_title": getattr(middle, "title", None),
                    "target_year": getattr(target, "publication_year", None),
                }
            )

    payload = {
        "center": _serialize_graph_neighbor(graph_paper),
        "outgoing_citations": outgoing[:20],
        "incoming_citations": incoming[:20],
        "two_hop_relations": relation_summaries,
        "neighbor_citation_count": len(neighbors["citations"]),
    }
    return (
        "学术引用图谱邻域（用于研究空白与相关工作定位，禁止编造图谱中不存在的关系）:\n"
        + json.dumps(payload, ensure_ascii=False)
    )


async def _run_analysis(
    context: TaskContext,
    *,
    run_id: str,
    kb_id: str,
    paper_id: str,
    model_spec: str,
    strategy: str = "multi_agent",
) -> dict[str, Any]:
    if strategy not in ANALYSIS_STRATEGIES:
        raise AcademicPaperAnalysisError("analysis_strategy_invalid", "不支持的论文分析策略")
    repo = AcademicPaperAnalysisRepository()
    initial_stage = "single_agent" if strategy == "single_agent" else "structure"
    await repo.update(run_id, {"status": "running", "started_at": _now(), "stage": initial_stage})
    stage_results: dict[str, Any] = {}
    try:
        paper, paper_context = await _paper_context(kb_id, paper_id)
        if strategy == "single_agent":
            await context.raise_if_cancelled()
            await context.set_progress(0, "正在执行单 Agent 全文分析")
            single_result = await _call_single_agent(model_spec, paper_context)
            stage_results = {"single_agent": single_result}
            result = {"paper_id": paper_id, "model": model_spec, "strategy": strategy, **single_result}
        else:
            await context.set_progress(5, "正在加载学术引用图谱邻域")
            graph_context = await _citation_graph_context(kb_id, paper)
            paper_context = f"{paper_context}\n\n{graph_context}"

            async def run_stage(stage: str, stage_input: str) -> dict[str, Any]:
                await context.raise_if_cancelled()
                await repo.update(run_id, {"stage": stage, "stage_results": stage_results})
                await context.set_progress(
                    {"structure": 0, "innovations": 25, "methodology": 50, "gaps": 75}[stage],
                    f"正在执行论文分析阶段：{stage}",
                )
                result = await _call_stage(model_spec, stage, stage_input)
                stage_results[stage] = result
                await repo.update(run_id, {"stage_results": stage_results})
                return result

            graph = build_analysis_workflow(run_stage)
            final_state = await graph.ainvoke({"paper_context": paper_context, "stage_results": {}})
            stage_results = final_state.get("stage_results") or stage_results
            result = {
                "paper_id": paper_id,
                "model": model_spec,
                "strategy": strategy,
                "structure": stage_results["structure"],
                "innovations": stage_results["innovations"],
                "methodology": stage_results["methodology"],
                "research_gaps": stage_results["gaps"],
            }
        await repo.update(
            run_id,
            {
                "status": "success",
                "stage": None,
                "stage_results": stage_results,
                "result": result,
                "completed_at": _now(),
            },
        )
        await context.set_result(result)
        await context.set_progress(100, "论文分析报告生成完成")
        return result
    except (Exception, asyncio.CancelledError) as exc:
        error_type = getattr(exc, "error_type", "analysis_failed")
        error_message = getattr(exc, "message", str(exc))
        await repo.update(
            run_id,
            {
                "status": "failed",
                "stage_results": stage_results,
                "error_type": error_type,
                "error_message": error_message,
                "completed_at": _now(),
            },
        )
        raise


async def enqueue_paper_analysis(
    *, kb_id: str, paper_id: str, current_user: User, model_spec: str | None
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    paper, _ = await _paper_context(kb_id, paper_id)
    resolved_model = (model_spec or config.default_model or "").strip()
    info = model_cache.get_model_info(resolved_model)
    if info is None or info.model_type != "chat" or not info.api_key:
        raise AcademicPaperAnalysisError("analysis_model_unavailable", "论文分析模型未配置或缺少 API Key")
    run_id = uuid.uuid4().hex
    repo = AcademicPaperAnalysisRepository()
    await repo.create(
        run_id=run_id,
        kb_id=kb_id,
        academic_paper_id=paper.id,
        uid=str(current_user.uid),
        model_config={"model": resolved_model, "provider": info.provider_type},
        strategy="multi_agent",
    )

    async def run(context: TaskContext):
        return await _run_analysis(
            context,
            run_id=run_id,
            kb_id=kb_id,
            paper_id=paper_id,
            model_spec=resolved_model,
            strategy="multi_agent",
        )

    try:
        task, created = await tasker.enqueue_unique_by_payload(
            name=f"论文分析 ({paper.title})",
            task_type="academic_paper_analysis",
            payload={"run_id": run_id, "kb_id": kb_id, "paper_id": paper_id, "model": resolved_model},
            payload_match={"kb_id": kb_id, "paper_id": paper_id},
            statuses={"pending", "running"},
            coroutine=run,
        )
        if not created:
            await repo.update(
                run_id,
                {
                    "status": "failed",
                    "error_type": "analysis_active",
                    "error_message": "当前论文已有分析任务运行中",
                    "completed_at": _now(),
                },
            )
            raise AcademicPaperAnalysisError("analysis_active", "当前论文已有分析任务运行中")
    except Exception as exc:
        if isinstance(exc, AcademicPaperAnalysisError):
            raise
        await repo.update(
            run_id,
            {
                "status": "failed",
                "error_type": "task_enqueue_failed",
                "error_message": str(exc),
                "completed_at": _now(),
            },
        )
        raise AcademicPaperAnalysisError("task_enqueue_failed", "论文分析任务提交失败") from exc
    return {"run_id": run_id, "task_id": task.id, "status": task.status}


async def recover_paper_analysis_runs() -> int:
    recovered = 0
    repo = AcademicPaperAnalysisRepository()
    for record in await repo.list_recoverable():
        model_spec = str((record.model_config_json or {}).get("model") or "")
        strategy = str(record.strategy or "multi_agent")
        if (record.model_config_json or {}).get("evaluation_id"):
            # 评测内运行由所属评测统一恢复，避免两条恢复链并发执行同一个 run_id。
            continue
        paper = await AcademicPaperRepository().get_by_id(record.academic_paper_id)
        if paper is None or not model_spec or strategy not in ANALYSIS_STRATEGIES:
            await repo.update(
                record.run_id,
                {
                    "status": "failed",
                    "error_type": "analysis_recovery_invalid",
                    "error_message": "论文分析恢复所需的论文或模型配置不存在",
                    "completed_at": _now(),
                },
            )
            continue

        async def run(
            context: TaskContext,
            item=record,
            paper_id=str(paper.paper_id),
            model=model_spec,
            item_strategy=strategy,
        ):
            return await _run_analysis(
                context,
                run_id=str(item.run_id),
                kb_id=str(item.kb_id),
                paper_id=paper_id,
                model_spec=model,
                strategy=item_strategy,
            )

        _, created = await tasker.enqueue_unique_by_payload(
            name=f"恢复论文分析 ({paper.title})",
            task_type="academic_paper_analysis",
            payload={
                "run_id": record.run_id,
                "kb_id": record.kb_id,
                "paper_id": paper.paper_id,
                "model": model_spec,
                "strategy": strategy,
            },
            payload_match={"run_id": record.run_id},
            statuses={"pending", "running"},
            coroutine=run,
        )
        recovered += int(created)
    return recovered


async def get_paper_analysis_run(*, run_id: str, current_user: User) -> dict[str, Any]:
    record = await AcademicPaperAnalysisRepository().get(run_id)
    if record is None:
        raise AcademicPaperAnalysisError("analysis_run_not_found", "论文分析运行不存在")
    if str(record.uid) != str(current_user.uid) and current_user.role not in {"admin", "superadmin"}:
        raise AcademicPaperAnalysisError("forbidden", "无权查看该论文分析运行")
    return AcademicPaperAnalysisRepository.serialize(record)


async def get_latest_paper_analysis(*, kb_id: str, paper_id: str, current_user: User) -> dict[str, Any] | None:
    await _ensure_access(current_user, kb_id)
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise AcademicPaperAnalysisError("paper_not_found", "论文不存在")
    record = await AcademicPaperAnalysisRepository().get_latest(kb_id=kb_id, academic_paper_id=paper.id)
    return AcademicPaperAnalysisRepository.serialize(record) if record else None


__all__ = [
    "ANALYSIS_STRATEGIES",
    "AcademicPaperAnalysisError",
    "enqueue_paper_analysis",
    "get_paper_analysis_run",
    "get_latest_paper_analysis",
    "recover_paper_analysis_runs",
]
