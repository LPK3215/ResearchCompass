from __future__ import annotations

import asyncio
import secrets
import uuid
from collections import Counter
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from yuxi.models.providers.cache import model_cache
from yuxi.repositories.academic_paper_analysis_evaluation_repository import AcademicPaperAnalysisEvaluationRepository
from yuxi.repositories.academic_paper_analysis_repository import AcademicPaperAnalysisRepository
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.services.academic_paper_analysis_service import ANALYSIS_STRATEGIES, _run_analysis
from yuxi.services.research_paper_service import _ensure_access
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.postgres.models_business import User


RUBRIC_VERSION = "analysis-blind-v1"
RUBRIC_DIMENSIONS = (
    "evidence_faithfulness",
    "coverage",
    "methodological_accuracy",
    "research_utility",
)
BLIND_LABELS = ("A", "B")


class AcademicPaperAnalysisEvaluationError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _serialize_run_result(run) -> dict[str, Any]:
    result = dict(run.result or {})
    result.pop("strategy", None)
    result.pop("model", None)
    result.pop("paper_id", None)
    return result


def _mean(values: list[int | float]) -> float | None:
    return round(float(mean(values)), 2) if values else None


def _duration_ms(run: Any) -> int | None:
    if not run.started_at or not run.completed_at:
        return None
    return max(round((run.completed_at - run.started_at).total_seconds() * 1000), 0)


class _EvaluationAnalysisContext:
    """Map one report run's progress into its pair's segment of the parent task."""

    def __init__(self, parent: TaskContext, start: float, end: float) -> None:
        self.parent = parent
        self.start = start
        self.end = end
        self.cancellation_reason: str | None = None

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        scaled = self.start + (self.end - self.start) * max(0.0, min(float(progress), 100.0)) / 100.0
        await self.parent.set_progress(scaled, message)

    async def set_result(self, result: Any) -> None:
        return None

    async def set_message(self, message: str) -> None:
        await self.parent.set_message(message)

    def is_cancel_requested(self) -> bool:
        return self.parent.is_cancel_requested()

    async def raise_if_cancelled(self) -> None:
        try:
            await self.parent.raise_if_cancelled()
        except asyncio.CancelledError:
            self.cancellation_reason = self.parent.cancellation_reason
            raise


class AcademicPaperAnalysisEvaluationService:
    def __init__(self) -> None:
        self.repo = AcademicPaperAnalysisEvaluationRepository()
        self.paper_repo = AcademicPaperRepository()
        self.analysis_repo = AcademicPaperAnalysisRepository()
        self.chunk_repo = KnowledgeChunkRepository()

    async def create_evaluation(
        self,
        *,
        kb_id: str,
        current_user: User,
        name: str,
        description: str,
        paper_ids: list[str],
        model_spec: str,
    ) -> dict[str, Any]:
        await _ensure_access(current_user, kb_id, write=True)
        normalized_paper_ids = list(dict.fromkeys(str(item).strip() for item in paper_ids if str(item).strip()))
        if not 2 <= len(normalized_paper_ids) <= 20:
            raise AcademicPaperAnalysisEvaluationError("invalid_papers", "每次分析对比需要选择 2 到 20 篇论文")
        papers = []
        for paper_id in normalized_paper_ids:
            paper = await self.paper_repo.get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
            if paper is None:
                raise AcademicPaperAnalysisEvaluationError("paper_not_found", f"论文不存在: {paper_id}")
            chunks, _ = await self.chunk_repo.list_academic_by_file_id(file_id=paper.file_id, offset=0, limit=1)
            if not chunks:
                raise AcademicPaperAnalysisEvaluationError(
                    "paper_content_missing", f"论文没有可用于分析的学术分块: {paper.title}"
                )
            papers.append(paper)

        resolved_model = model_spec.strip()
        info = model_cache.get_model_info(resolved_model)
        if info is None or info.model_type != "chat" or not info.api_key:
            raise AcademicPaperAnalysisEvaluationError("analysis_model_unavailable", "评测模型未配置或缺少 API Key")

        evaluation_id = f"analysis_eval_{uuid.uuid4().hex[:12]}"
        items = []
        for index, paper in enumerate(papers):
            is_single_first = bool(secrets.randbits(1))
            items.append(
                {
                    "item_id": f"analysis_eval_item_{uuid.uuid4().hex[:12]}",
                    "evaluation_id": evaluation_id,
                    "academic_paper_id": paper.id,
                    "item_index": index,
                    "blind_assignment": {
                        "A": "single_agent" if is_single_first else "multi_agent",
                        "B": "multi_agent" if is_single_first else "single_agent",
                    },
                    "status": "pending",
                }
            )
        await self.repo.create_with_items(
            {
                "evaluation_id": evaluation_id,
                "kb_id": kb_id,
                "name": name.strip(),
                "description": description.strip(),
                "model_config_json": {"model": resolved_model, "provider": info.provider_type, "temperature": 0},
                "rubric_version": RUBRIC_VERSION,
                "status": "queued",
                "paper_count": len(items),
                "completed_pairs": 0,
                "created_by": str(current_user.uid),
            },
            items,
        )

        async def run(context: TaskContext):
            return await self._run_evaluation(context, evaluation_id=evaluation_id)

        try:
            task, created = await tasker.enqueue_unique_by_payload(
                name=f"单/多 Agent 分析对比 ({name.strip()})",
                task_type="academic_paper_analysis_evaluation",
                payload={"evaluation_id": evaluation_id, "kb_id": kb_id},
                payload_match={"evaluation_id": evaluation_id},
                statuses={"pending", "running"},
                coroutine=run,
            )
            if not created:
                raise RuntimeError("分析对比任务已存在")
        except Exception as exc:
            await self.repo.update_evaluation(
                evaluation_id,
                {"status": "failed", "error_message": str(exc), "completed_at": _now()},
            )
            raise AcademicPaperAnalysisEvaluationError("task_enqueue_failed", "分析对比任务提交失败") from exc
        await self.repo.update_evaluation(evaluation_id, {"task_id": task.id})
        return {"evaluation_id": evaluation_id, "task_id": task.id, "status": "queued"}

    async def _ensure_pair_run(
        self,
        *,
        context: TaskContext,
        evaluation: Any,
        item: Any,
        paper: Any,
        strategy: str,
        progress_start: float,
        progress_end: float,
    ) -> str:
        if strategy not in ANALYSIS_STRATEGIES:
            raise AcademicPaperAnalysisEvaluationError("analysis_strategy_invalid", "分析对比包含不支持的策略")
        run_field = "single_run_id" if strategy == "single_agent" else "multi_run_id"
        run_id = str(getattr(item, run_field) or "")
        if run_id:
            existing = await self.repo.get_run(run_id)
            if existing is not None and existing.status == "success":
                return run_id
        else:
            run_id = uuid.uuid4().hex
            await self.analysis_repo.create(
                run_id=run_id,
                kb_id=str(evaluation.kb_id),
                academic_paper_id=paper.id,
                uid=str(evaluation.created_by),
                model_config={
                    **dict(evaluation.model_config_json or {}),
                    "evaluation_id": evaluation.evaluation_id,
                    "evaluation_item_id": item.item_id,
                },
                strategy=strategy,
            )
            await self.repo.update_item(item.item_id, {run_field: run_id})

        model_spec = str((evaluation.model_config_json or {}).get("model") or "")
        if not model_spec:
            raise AcademicPaperAnalysisEvaluationError("analysis_model_unavailable", "分析对比缺少固定模型配置")
        await _run_analysis(
            _EvaluationAnalysisContext(context, progress_start, progress_end),
            run_id=run_id,
            kb_id=str(evaluation.kb_id),
            paper_id=str(paper.paper_id),
            model_spec=model_spec,
            strategy=strategy,
        )
        return run_id

    async def _run_evaluation(self, context: TaskContext, *, evaluation_id: str) -> dict[str, Any]:
        evaluation = await self.repo.get(evaluation_id)
        if evaluation is None:
            raise AcademicPaperAnalysisEvaluationError("evaluation_not_found", "分析对比不存在")
        await self.repo.update_evaluation(
            evaluation_id, {"status": "running", "started_at": _now(), "error_message": None}
        )
        items = await self.repo.list_items(evaluation_id)
        try:
            for index, item in enumerate(items):
                await context.raise_if_cancelled()
                if item.status == "completed":
                    continue
                pair_start = index * 100 / len(items)
                pair_mid = pair_start + 50 / len(items)
                pair_end = (index + 1) * 100 / len(items)
                paper = await self.repo.get_paper(item.academic_paper_id)
                if paper is None:
                    raise AcademicPaperAnalysisEvaluationError("paper_not_found", "分析对比关联论文不存在")
                await self.repo.update_item(item.item_id, {"status": "running", "error_message": None})
                try:
                    single_run_id = await self._ensure_pair_run(
                        context=context,
                        evaluation=evaluation,
                        item=item,
                        paper=paper,
                        strategy="single_agent",
                        progress_start=pair_start,
                        progress_end=pair_mid,
                    )
                    multi_run_id = await self._ensure_pair_run(
                        context=context,
                        evaluation=evaluation,
                        item=item,
                        paper=paper,
                        strategy="multi_agent",
                        progress_start=pair_mid,
                        progress_end=pair_end,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self.repo.update_item(
                        item.item_id,
                        {"status": "failed", "error_message": str(exc), "completed_at": _now()},
                    )
                else:
                    await self.repo.update_item(
                        item.item_id,
                        {
                            "single_run_id": single_run_id,
                            "multi_run_id": multi_run_id,
                            "status": "completed",
                            "completed_at": _now(),
                        },
                    )
                current_items = await self.repo.list_items(evaluation_id)
                completed = len([entry for entry in current_items if entry.status == "completed"])
                await self.repo.update_evaluation(evaluation_id, {"completed_pairs": completed})

            final_items = await self.repo.list_items(evaluation_id)
            final_status = (
                "completed_with_failures" if any(item.status == "failed" for item in final_items) else "completed"
            )
            await self.repo.update_evaluation(
                evaluation_id, {"status": final_status, "completed_at": _now()}
            )
            completed_pairs = len([item for item in final_items if item.status == "completed"])
            result = {"evaluation_id": evaluation_id, "completed_pairs": completed_pairs}
            await context.set_result(result)
            await context.set_progress(100, "单/多 Agent 对比报告已生成，等待人工盲审")
            return result
        except (Exception, asyncio.CancelledError) as exc:
            message = "任务已取消" if isinstance(exc, asyncio.CancelledError) else str(exc)
            if isinstance(exc, asyncio.CancelledError) and context.cancellation_reason == "shutdown":
                raise
            await self.repo.update_evaluation(
                evaluation_id, {"status": "failed", "error_message": message, "completed_at": _now()}
            )
            raise

    async def _get_evaluation(self, *, kb_id: str, evaluation_id: str, current_user: User, write: bool = False):
        await _ensure_access(current_user, kb_id, write=write)
        evaluation = await self.repo.get(evaluation_id)
        if evaluation is None or str(evaluation.kb_id) != str(kb_id):
            raise AcademicPaperAnalysisEvaluationError("evaluation_not_found", "分析对比不存在")
        return evaluation

    async def list_evaluations(self, *, kb_id: str, current_user: User) -> list[dict[str, Any]]:
        await _ensure_access(current_user, kb_id)
        return [
            self._serialize_evaluation(item, include_model=False)
            for item in await self.repo.list_by_kb_id(kb_id)
        ]

    async def list_blind_items(
        self, *, kb_id: str, evaluation_id: str, current_user: User
    ) -> list[dict[str, Any]]:
        await self._get_evaluation(kb_id=kb_id, evaluation_id=evaluation_id, current_user=current_user)
        scores = await self.repo.list_scores(evaluation_id)
        scored_item_ids = {score.item_id for score in scores if score.scorer_uid == str(current_user.uid)}
        result = []
        for item in await self.repo.list_items(evaluation_id):
            if item.status != "completed":
                continue
            paper = await self.repo.get_paper(item.academic_paper_id)
            if paper is None:
                continue
            result.append(
                {
                    "item_id": item.item_id,
                    "paper": {"paper_id": paper.paper_id, "title": paper.title},
                    "status": item.status,
                    "scored": item.item_id in scored_item_ids,
                }
            )
        return result

    async def get_blind_item(
        self, *, kb_id: str, evaluation_id: str, item_id: str, current_user: User
    ) -> dict[str, Any]:
        await self._get_evaluation(kb_id=kb_id, evaluation_id=evaluation_id, current_user=current_user)
        item = await self.repo.get_item(item_id)
        if item is None or item.evaluation_id != evaluation_id:
            raise AcademicPaperAnalysisEvaluationError("evaluation_item_not_found", "盲审条目不存在")
        if item.status != "completed" or not item.single_run_id or not item.multi_run_id:
            raise AcademicPaperAnalysisEvaluationError("evaluation_item_not_ready", "该盲审条目尚未完成")
        paper, single_run, multi_run = await asyncio.gather(
            self.repo.get_paper(item.academic_paper_id),
            self.repo.get_run(item.single_run_id),
            self.repo.get_run(item.multi_run_id),
        )
        if paper is None or single_run is None or multi_run is None or not single_run.result or not multi_run.result:
            raise AcademicPaperAnalysisEvaluationError("evaluation_item_not_ready", "盲审报告不可用")
        runs_by_strategy = {"single_agent": single_run, "multi_agent": multi_run}
        assignment = item.blind_assignment or {}
        reports = {
            label: _serialize_run_result(runs_by_strategy[strategy])
            for label, strategy in assignment.items()
            if label in BLIND_LABELS and strategy in runs_by_strategy
        }
        if set(reports) != set(BLIND_LABELS):
            raise AcademicPaperAnalysisEvaluationError("evaluation_item_invalid", "盲审分配记录无效")
        own_score = next(
            (
                score
                for score in await self.repo.list_scores(evaluation_id)
                if score.item_id == item_id and score.scorer_uid == str(current_user.uid)
            ),
            None,
        )
        return {
            "item_id": item.item_id,
            "paper": {"paper_id": paper.paper_id, "title": paper.title},
            "rubric": {"version": RUBRIC_VERSION, "dimensions": list(RUBRIC_DIMENSIONS)},
            "reports": reports,
            "own_score": own_score.blind_scores if own_score else None,
            "own_notes": own_score.notes if own_score else "",
        }

    async def submit_blind_score(
        self,
        *,
        kb_id: str,
        evaluation_id: str,
        item_id: str,
        current_user: User,
        blind_scores: dict[str, Any],
        notes: str,
    ) -> dict[str, Any]:
        await self.get_blind_item(
            kb_id=kb_id, evaluation_id=evaluation_id, item_id=item_id, current_user=current_user
        )
        self._validate_blind_scores(blind_scores)
        score = await self.repo.upsert_score(
            evaluation_id=evaluation_id,
            item_id=item_id,
            scorer_uid=str(current_user.uid),
            blind_scores=blind_scores,
            notes=notes.strip(),
        )
        return {"score_id": score.score_id, "submitted_at": score.submitted_at.isoformat()}

    @staticmethod
    def _validate_blind_scores(scores: dict[str, Any]) -> None:
        if set(scores) != {"A", "B", "preference"}:
            raise AcademicPaperAnalysisEvaluationError("invalid_score", "盲审评分必须包含 A、B 和 preference")
        if scores["preference"] not in {"A", "B", "tie"}:
            raise AcademicPaperAnalysisEvaluationError("invalid_score", "preference 必须是 A、B 或 tie")
        for label in BLIND_LABELS:
            values = scores[label]
            if not isinstance(values, dict) or set(values) != set(RUBRIC_DIMENSIONS):
                raise AcademicPaperAnalysisEvaluationError("invalid_score", f"报告 {label} 必须完成全部量表项")
            is_invalid = any(
                not isinstance(values[key], int)
                or isinstance(values[key], bool)
                or not 1 <= values[key] <= 5
                for key in RUBRIC_DIMENSIONS
            )
            if is_invalid:
                raise AcademicPaperAnalysisEvaluationError("invalid_score", f"报告 {label} 的量表项必须为 1–5 的整数")

    async def get_report(self, *, kb_id: str, evaluation_id: str, current_user: User) -> dict[str, Any]:
        if current_user.role not in {"admin", "superadmin"}:
            raise AcademicPaperAnalysisEvaluationError("forbidden", "只有管理员可以查看去盲后的汇总报告")
        evaluation = await self._get_evaluation(
            kb_id=kb_id, evaluation_id=evaluation_id, current_user=current_user, write=True
        )
        items = await self.repo.list_items(evaluation_id)
        scores = await self.repo.list_scores(evaluation_id)
        items_by_id = {item.item_id: item for item in items}
        strategy_values = {
            strategy: {dimension: [] for dimension in RUBRIC_DIMENSIONS} for strategy in ANALYSIS_STRATEGIES
        }
        preferences = Counter({"single_agent": 0, "multi_agent": 0, "tie": 0})
        for score in scores:
            item = items_by_id.get(score.item_id)
            if item is None:
                continue
            assignment = item.blind_assignment or {}
            values = score.blind_scores or {}
            for label in BLIND_LABELS:
                strategy = assignment.get(label)
                if strategy not in strategy_values or not isinstance(values.get(label), dict):
                    continue
                for dimension in RUBRIC_DIMENSIONS:
                    value = values[label].get(dimension)
                    if isinstance(value, int) and not isinstance(value, bool):
                        strategy_values[strategy][dimension].append(value)
            preference = values.get("preference")
            preferences[assignment.get(preference, "tie") if preference in BLIND_LABELS else "tie"] += 1

        strategy_summary = {
            strategy: {
                "score_count": max((len(values) for values in dimensions.values()), default=0),
                "dimension_means": {dimension: _mean(values) for dimension, values in dimensions.items()},
                "overall_mean": _mean([value for values in dimensions.values() for value in values]),
            }
            for strategy, dimensions in strategy_values.items()
        }
        duration_values = {strategy: [] for strategy in ANALYSIS_STRATEGIES}
        serialized_items = []
        for item in items:
            single_run = await self.repo.get_run(item.single_run_id) if item.single_run_id else None
            multi_run = await self.repo.get_run(item.multi_run_id) if item.multi_run_id else None
            durations = {
                "single_agent": _duration_ms(single_run) if single_run else None,
                "multi_agent": _duration_ms(multi_run) if multi_run else None,
            }
            for strategy, duration in durations.items():
                if duration is not None:
                    duration_values[strategy].append(duration)
            serialized_items.append(
                {
                    "item_id": item.item_id,
                    "status": item.status,
                    "error_message": item.error_message,
                    "single_run_id": item.single_run_id,
                    "multi_run_id": item.multi_run_id,
                    "duration_ms": durations,
                }
            )
        for strategy, durations in duration_values.items():
            strategy_summary[strategy]["duration_mean_ms"] = _mean(durations)
        return {
            **self._serialize_evaluation(evaluation, include_model=True),
            "rubric": {"version": evaluation.rubric_version, "dimensions": list(RUBRIC_DIMENSIONS)},
            "items": serialized_items,
            "score_count": len(scores),
            "strategy_summary": strategy_summary,
            "preference_counts": dict(preferences),
        }

    @staticmethod
    def _serialize_evaluation(evaluation, *, include_model: bool) -> dict[str, Any]:
        serialized = {
            "evaluation_id": evaluation.evaluation_id,
            "kb_id": evaluation.kb_id,
            "name": evaluation.name,
            "description": evaluation.description or "",
            "rubric_version": evaluation.rubric_version,
            "status": evaluation.status,
            "paper_count": evaluation.paper_count,
            "completed_pairs": evaluation.completed_pairs,
            "task_id": evaluation.task_id,
            "error_message": evaluation.error_message,
            "created_at": evaluation.created_at.isoformat() if evaluation.created_at else None,
            "started_at": evaluation.started_at.isoformat() if evaluation.started_at else None,
            "completed_at": evaluation.completed_at.isoformat() if evaluation.completed_at else None,
        }
        if include_model:
            serialized["model_config"] = evaluation.model_config_json or {}
        return serialized

    async def recover_evaluations(self) -> int:
        recovered = 0
        for evaluation in await self.repo.list_recoverable():
            async def run(context: TaskContext, item_id=str(evaluation.evaluation_id)):
                return await self._run_evaluation(context, evaluation_id=item_id)

            _, created = await tasker.enqueue_unique_by_payload(
                name=f"恢复单/多 Agent 分析对比 ({evaluation.name})",
                task_type="academic_paper_analysis_evaluation",
                payload={"evaluation_id": evaluation.evaluation_id, "kb_id": evaluation.kb_id},
                payload_match={"evaluation_id": evaluation.evaluation_id},
                statuses={"pending", "running"},
                coroutine=run,
            )
            recovered += int(created)
        return recovered


__all__ = [
    "AcademicPaperAnalysisEvaluationError",
    "AcademicPaperAnalysisEvaluationService",
    "RUBRIC_DIMENSIONS",
    "RUBRIC_VERSION",
]
