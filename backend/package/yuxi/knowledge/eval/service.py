import asyncio
import hashlib
import json
import re
import uuid
from typing import Any

from yuxi.knowledge.eval.benchmark_generation import (
    dump_benchmark_item,
    iter_generated_benchmark_items,
    normalize_generation_concurrency_count,
)
from yuxi.knowledge.eval.evaluator import aggregate_metrics, evaluate_question
from yuxi.knowledge.eval.metrics import EvaluationMetricsCalculator
from yuxi.knowledge.runtime import knowledge_base
from yuxi.models import select_model
from yuxi.repositories.evaluation_repository import EvaluationRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.task_service import TaskContext, tasker
from yuxi.utils import logger
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive


EXPERIMENT_VARIANT_LIMIT = 8
_EXPERIMENT_METRIC_ORDER = ("overall_score", "answer_correctness", "recall@10", "f1@10", "recall@5", "f1@5")


def build_evaluation_run_name(started_at=None, hash_value: str | None = None) -> str:
    date_part = (started_at or utc_now_naive()).strftime("%Y%m%d")
    hash_part = re.sub(r"[^a-fA-F0-9]", "", hash_value or uuid.uuid4().hex).lower()[:6]
    if len(hash_part) < 6:
        hash_part = (hash_part + uuid.uuid4().hex)[:6]
    return f"eval-{date_part}-{hash_part}"


def _normalize_experiment_variant_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("实验变体检索配置必须是对象")
    return {str(key): value for key, value in config.items()}


def _dataset_fingerprint(items: list[Any]) -> str:
    payload = [
        {
            "item_id": str(item.item_id),
            "query": item.query_text,
            "gold_chunk_ids": item.gold_chunk_ids or [],
            "gold_answer": item.gold_answer,
        }
        for item in items
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _corpus_fingerprint(content_hashes: list[str]) -> str:
    encoded = json.dumps(sorted(content_hashes), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metric_delta(value: Any, baseline_value: Any) -> float | None:
    if not isinstance(value, (int, float)) or not isinstance(baseline_value, (int, float)):
        return None
    return float(value) - float(baseline_value)


class _ExperimentEvaluationContext:
    """将单个变体的评估进度映射到所属实验任务。"""

    def __init__(
        self, parent: TaskContext, payload: dict[str, Any], progress_start: float, progress_end: float
    ) -> None:
        self.parent = parent
        self.payload = payload
        self.progress_start = progress_start
        self.progress_end = progress_end
        self.cancellation_reason: str | None = None

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        scaled = (
            self.progress_start + (self.progress_end - self.progress_start) * max(0.0, min(progress, 100.0)) / 100.0
        )
        await self.parent.set_progress(scaled, message)

    async def set_message(self, message: str) -> None:
        await self.parent.set_message(message)

    async def set_result(self, result: Any) -> None:
        await self.parent.set_result(result)

    def is_cancel_requested(self) -> bool:
        return self.parent.is_cancel_requested()

    async def raise_if_cancelled(self) -> None:
        try:
            await self.parent.raise_if_cancelled()
        except asyncio.CancelledError:
            self.cancellation_reason = self.parent.cancellation_reason
            raise


class EvaluationService:
    """RAG评估服务"""

    def __init__(self):
        self.eval_repo = EvaluationRepository()
        self.kb_repo = KnowledgeBaseRepository()
        self.chunk_repo = KnowledgeChunkRepository()
        self.file_repo = KnowledgeFileRepository()
        self.task_repo = TaskRepository()

    def _dataset_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row.dataset_id,
            "dataset_id": row.dataset_id,
            "name": row.name,
            "description": row.description,
            "kb_id": row.kb_id,
            "item_count": row.item_count,
            "has_gold_chunks": row.has_gold_chunks,
            "has_gold_answers": row.has_gold_answers,
            "build_metadata": row.build_metadata or {},
            "created_by": row.created_by,
            "created_at": format_utc_datetime(row.created_at),
            "updated_at": format_utc_datetime(row.updated_at),
        }

    def _dataset_item_to_dict(self, item) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "item_index": item.item_index,
            "query": item.query_text,
            "gold_chunk_ids": item.gold_chunk_ids or [],
            "gold_answer": item.gold_answer,
        }

    def _run_item_to_dict(self, item) -> dict[str, Any]:
        return {
            "query": item.query_text,
            "gold_chunk_ids": item.gold_chunk_ids,
            "gold_document_hashes": item.gold_document_hashes or [],
            "gold_answer": item.gold_answer,
            "generated_answer": item.generated_answer,
            "retrieved_chunks": item.retrieved_chunks,
            "metrics": item.metrics or {},
        }

    async def _get_indexed_corpus_snapshot(self, kb_id: str) -> dict[str, Any]:
        kb = await self.kb_repo.get_by_kb_id(kb_id)
        if kb is None:
            raise ValueError(f"对照知识库不存在: {kb_id}")
        if (kb.kb_type or "").lower() != "milvus":
            raise ValueError("消融实验仅支持 Milvus 知识库")

        records = await self.file_repo.list_by_kb_id(kb_id)
        indexed_hashes = sorted(
            [
                str(record.content_hash).strip()
                for record in records
                if not record.is_folder
                and record.status in {"indexed", "done"}
                and isinstance(record.content_hash, str)
                and record.content_hash.strip()
            ]
        )
        if not indexed_hashes:
            raise ValueError(f"对照知识库没有可用于评估的已索引文档: {kb_id}")
        return {
            "kb_id": str(kb.kb_id),
            "embedding_model_spec": str(kb.embedding_model_spec or ""),
            "content_hashes": indexed_hashes,
            "corpus_fingerprint": _corpus_fingerprint(indexed_hashes),
        }

    async def _build_dataset_document_gold(self, dataset_items: list[Any]) -> dict[str, list[str]]:
        chunk_ids = sorted(
            {
                str(chunk_id)
                for item in dataset_items
                for chunk_id in (item.gold_chunk_ids or [])
                if isinstance(chunk_id, str) and chunk_id
            }
        )
        chunks = await self.chunk_repo.list_by_chunk_ids(chunk_ids)
        file_ids = sorted({str(chunk.file_id) for chunk in chunks if chunk.file_id})
        files = await self.file_repo.list_by_file_ids(file_ids)
        content_hash_by_file_id = {
            str(file.file_id): str(file.content_hash).strip()
            for file in files
            if isinstance(file.content_hash, str) and file.content_hash.strip()
        }
        document_hashes_by_chunk_id = {
            str(chunk.chunk_id): content_hash_by_file_id.get(str(chunk.file_id), "") for chunk in chunks
        }
        missing = [chunk_id for chunk_id in chunk_ids if not document_hashes_by_chunk_id.get(chunk_id)]
        if missing:
            raise ValueError("评估基准包含无法映射到源文档的 gold chunk，不能用于跨分块对比")
        return {
            str(item.item_id): list(
                dict.fromkeys(document_hashes_by_chunk_id[str(chunk_id)] for chunk_id in (item.gold_chunk_ids or []))
            )
            for item in dataset_items
        }

    async def _ensure_experiment_variant_corpus(self, experiment: Any, variant: Any) -> None:
        snapshot = experiment.corpus_snapshot or {}
        expected_hashes = snapshot.get("content_hashes")
        if not isinstance(expected_hashes, list) or not expected_hashes:
            raise ValueError("实验缺少语料快照，无法保证对比条件一致")
        current = await self._get_indexed_corpus_snapshot(str(variant.kb_id))
        if current["content_hashes"] != expected_hashes:
            raise ValueError("实验运行期间知识库语料发生变化，已拒绝产生不可复现的对比结果")
        if current["embedding_model_spec"] != str(snapshot.get("embedding_model_spec") or ""):
            raise ValueError("实验运行期间嵌入模型发生变化，已拒绝产生不可复现的对比结果")

    def _is_error_run_item(self, item) -> bool:
        metrics = item.metrics or {}
        return metrics.get("score", 1.0) <= 0.5 or any(
            metrics.get(key, 1.0) < 0.3 for key in metrics if key.startswith("recall@")
        )

    def _normalize_run_name(self, name: str | None, run_id: str) -> str:
        run_name = (name or "").strip()
        if run_name:
            return run_name
        return build_evaluation_run_name(hash_value=run_id.removeprefix("run_"))

    def _run_name_from_row(self, row) -> str:
        name = (getattr(row, "name", None) or "").strip()
        if name:
            return name
        return build_evaluation_run_name(row.started_at, hash_value=row.run_id.removeprefix("run_"))

    async def _sync_dataset_build_metadata(self, row) -> None:
        metadata = dict(row.build_metadata or {})
        if metadata.get("source") != "generated" or metadata.get("status") not in {"pending", "running"}:
            return

        task_id = metadata.get("task_id")
        task = await self.task_repo.get_by_id(task_id) if task_id else None
        if task is None:
            metadata.pop("progress", None)
            metadata.update(status="failed", message="生成任务不存在")
        elif task.status == "success":
            metadata.update(status="completed", progress=100, message=task.message or "完成")
        elif task.status in {"failed", "cancelled"}:
            metadata.pop("progress", None)
            metadata.update(status="failed", message=task.error or task.message or "生成任务失败")
        else:
            metadata.update(status=task.status, progress=task.progress, message=task.message)

        if metadata != (row.build_metadata or {}):
            await self.eval_repo.update_dataset(row.dataset_id, {"build_metadata": metadata})
            row.build_metadata = metadata

    def _build_dataset_items(
        self, dataset_id: str, kb_id: str, questions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [
            {
                "item_id": f"dataset_item_{uuid.uuid4().hex[:12]}",
                "dataset_id": dataset_id,
                "kb_id": kb_id,
                "item_index": index,
                "query_text": item["query"],
                "gold_chunk_ids": item.get("gold_chunk_ids") or [],
                "gold_answer": item.get("gold_answer"),
            }
            for index, item in enumerate(questions)
        ]

    def _build_jsonl_content(self, items: list[Any]) -> str:
        lines = []
        for item in items:
            payload = {"query": item.query_text}
            if item.gold_chunk_ids:
                payload["gold_chunk_ids"] = item.gold_chunk_ids
            if item.gold_answer:
                payload["gold_answer"] = item.gold_answer
            lines.append(dump_benchmark_item(payload).rstrip("\n"))
        return "\n".join(lines) + ("\n" if lines else "")

    def _safe_jsonl_filename(self, name: str | None, fallback: str) -> str:
        filename = (name or "").strip() or fallback
        filename = re.sub(r"[\\/:*?\"<>|]+", "_", filename).strip()
        if not filename or filename in {".", ".."}:
            filename = fallback
        return filename if filename.endswith(".jsonl") else f"{filename}.jsonl"

    def _parse_jsonl_questions(self, file_content: bytes) -> tuple[list[dict[str, Any]], bool, bool]:
        questions = []
        has_gold_chunks = False
        has_gold_answers = False
        content = file_content.decode("utf-8")

        for line_num, line in enumerate(content.strip().split("\n"), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"第{line_num}行JSON格式错误: {str(e)}")
            if "query" not in item:
                raise ValueError(f"第{line_num}行缺少必需的'query'字段")
            if item.get("gold_chunk_ids"):
                has_gold_chunks = True
            if item.get("gold_answer"):
                has_gold_answers = True
            questions.append(item)

        if not questions:
            raise ValueError("文件中没有有效的问题数据")
        return questions, has_gold_chunks, has_gold_answers

    async def upload_dataset(
        self, kb_id: str, file_content: bytes, filename: str, name: str, description: str, created_by: str
    ) -> dict[str, Any]:
        try:
            questions, has_gold_chunks, has_gold_answers = self._parse_jsonl_questions(file_content)
            dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
            dataset_name = name.strip() or filename or dataset_id

            row = await self.eval_repo.create_dataset_with_items(
                {
                    "dataset_id": dataset_id,
                    "kb_id": kb_id,
                    "name": dataset_name,
                    "description": description,
                    "item_count": len(questions),
                    "has_gold_chunks": has_gold_chunks,
                    "has_gold_answers": has_gold_answers,
                    "build_metadata": {
                        "source": "upload",
                        "status": "completed",
                        "progress": 100,
                        "filename": filename,
                    },
                    "created_by": created_by,
                },
                self._build_dataset_items(dataset_id, kb_id, questions),
            )
            return self._dataset_to_dict(row)
        except Exception as e:
            logger.error(f"上传评估数据集失败: {e}")
            raise

    async def list_datasets(self, kb_id: str) -> list[dict[str, Any]]:
        try:
            rows = await self.eval_repo.list_datasets(kb_id)
            for row in rows:
                await self._sync_dataset_build_metadata(row)
            return [self._dataset_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"获取评估数据集列表失败: {e}")
            raise

    async def get_dataset_detail(
        self, kb_id: str, dataset_id: str, page: int = 1, page_size: int = 10
    ) -> dict[str, Any]:
        try:
            row = await self.eval_repo.get_dataset(dataset_id)
            if row is None or row.kb_id != kb_id:
                raise ValueError("Dataset not found")
            if (row.build_metadata or {}).get("status", "completed") != "completed":
                raise ValueError("Dataset is not ready")

            total_items = await self.eval_repo.count_dataset_items(dataset_id)
            items = await self.eval_repo.list_dataset_items(dataset_id, (page - 1) * page_size, page_size)
            total_pages = (total_items + page_size - 1) // page_size
            data = self._dataset_to_dict(row)
            data.update(
                {
                    "items": [self._dataset_item_to_dict(item) for item in items],
                    "pagination": {
                        "current_page": page,
                        "page_size": page_size,
                        "total_items": total_items,
                        "total_pages": total_pages,
                        "has_next": page < total_pages,
                        "has_prev": page > 1,
                    },
                }
            )
            return data
        except Exception as e:
            logger.error(f"获取评估数据集详情失败: {e}")
            raise

    async def export_dataset_jsonl(self, dataset_id: str) -> dict[str, str]:
        row = await self.eval_repo.get_dataset(dataset_id)
        if row is None:
            raise ValueError("Dataset not found")
        if (row.build_metadata or {}).get("status", "completed") != "completed":
            raise ValueError("Dataset is not ready")
        items = await self.eval_repo.list_all_dataset_items(dataset_id)
        return {
            "filename": self._safe_jsonl_filename(row.name, row.dataset_id),
            "content": self._build_jsonl_content(items),
        }

    async def export_external_baseline_template(self, kb_id: str, dataset_id: str) -> dict[str, str]:
        """导出外部基线逐题结果模板，并固化数据集/语料指纹。"""
        dataset = await self.eval_repo.get_dataset(dataset_id)
        if dataset is None or str(dataset.kb_id) != str(kb_id):
            raise ValueError("Dataset not found")
        if (dataset.build_metadata or {}).get("status", "completed") != "completed":
            raise ValueError("Dataset is not ready")
        items = await self.eval_repo.list_all_dataset_items(dataset_id)
        if not items:
            raise ValueError("Dataset has no items")
        corpus = await self._get_indexed_corpus_snapshot(kb_id)
        lines = [
            dump_benchmark_item(
                {
                    "record_type": "manifest",
                    "schema_version": "research_compass_external_baseline.v1",
                    "dataset_id": dataset_id,
                    "dataset_fingerprint": _dataset_fingerprint(items),
                    "corpus_fingerprint": corpus["corpus_fingerprint"],
                    "item_count": len(items),
                }
            ).rstrip("\n")
        ]
        lines.extend(
            dump_benchmark_item(
                {
                    "record_type": "result",
                    "item_id": item.item_id,
                    "query": item.query_text,
                    "generated_answer": "",
                    "retrieved_document_hashes": [],
                }
            ).rstrip("\n")
            for item in items
        )
        return {
            "filename": self._safe_jsonl_filename(f"{dataset.name}-external-baseline", f"{dataset_id}-baseline"),
            "content": "\n".join(lines) + "\n",
        }

    def _normalize_external_variant(
        self,
        *,
        variant: dict[str, Any],
        dataset_items: list[Any],
        dataset_fingerprint: str,
        corpus_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        execution_type = str(variant.get("execution_type") or "research_compass")
        if execution_type not in {"external_rag", "direct_llm"}:
            raise ValueError("外部基线变体类型必须是 external_rag 或 direct_llm")
        if variant.get("kb_id") and str(variant["kb_id"]) != str(corpus_snapshot["kb_id"]):
            raise ValueError("外部基线只能使用实验源知识库的语料快照")
        if str(variant.get("dataset_fingerprint") or "") != dataset_fingerprint:
            raise ValueError("外部基线的数据集指纹与当前评估基准不一致")
        if str(variant.get("corpus_fingerprint") or "") != str(corpus_snapshot["corpus_fingerprint"]):
            raise ValueError("外部基线的语料指纹与当前知识库不一致")

        provenance = variant.get("provenance")
        if not isinstance(provenance, dict) or not str(provenance.get("system_name") or "").strip():
            raise ValueError("外部基线必须提供来源系统名称")
        results = variant.get("external_results")
        if not isinstance(results, list) or not results:
            raise ValueError("外部基线必须提供逐题结果")
        if len(results) != len(dataset_items):
            raise ValueError("外部基线逐题结果数量必须与评估基准完全一致")

        expected_items = {str(item.item_id): item for item in dataset_items}
        normalized_results: list[dict[str, Any]] = []
        seen_item_ids: set[str] = set()
        source_hashes = set(corpus_snapshot["content_hashes"])
        for index, result in enumerate(results, start=1):
            if not isinstance(result, dict):
                raise ValueError(f"外部基线第 {index} 条结果必须是对象")
            item_id = str(result.get("item_id") or "").strip()
            if item_id not in expected_items or item_id in seen_item_ids:
                raise ValueError(f"外部基线第 {index} 条结果的 item_id 无效或重复")
            query = result.get("query")
            if not isinstance(query, str) or query != expected_items[item_id].query_text:
                raise ValueError(f"外部基线第 {index} 条结果的 query 与评估基准不一致")
            answer = result.get("generated_answer") or ""
            if not isinstance(answer, str) or len(answer) > 200000:
                raise ValueError(f"外部基线第 {index} 条结果的 generated_answer 无效")
            document_hashes = result.get("retrieved_document_hashes") or []
            if not isinstance(document_hashes, list) or len(document_hashes) > 100:
                raise ValueError(f"外部基线第 {index} 条结果的 retrieved_document_hashes 无效")
            document_hashes = list(dict.fromkeys(str(value).strip() for value in document_hashes if str(value).strip()))
            if execution_type == "direct_llm" and document_hashes:
                raise ValueError("direct_llm 基线不得提供检索文档哈希")
            if execution_type == "direct_llm" and not answer.strip():
                raise ValueError(f"direct_llm 基线第 {index} 条结果缺少 generated_answer")
            if not set(document_hashes).issubset(source_hashes):
                raise ValueError(f"外部基线第 {index} 条结果引用了不属于实验语料的文档")
            normalized_results.append(
                {
                    "item_id": item_id,
                    "query": query,
                    "generated_answer": answer,
                    "retrieved_document_hashes": document_hashes,
                }
            )
            seen_item_ids.add(item_id)
        if seen_item_ids != set(expected_items):
            raise ValueError("外部基线缺少评估基准题目结果")
        return {
            "execution_type": execution_type,
            "provenance": {
                "system_name": str(provenance["system_name"]).strip(),
                "system_version": str(provenance.get("system_version") or "").strip() or None,
                "run_id": str(provenance.get("run_id") or "").strip() or None,
                "notes": str(provenance.get("notes") or "").strip() or None,
                "configuration": provenance.get("configuration") or {},
            },
            "input_snapshot": {
                "schema_version": "research_compass_external_baseline.v1",
                "dataset_fingerprint": dataset_fingerprint,
                "corpus_fingerprint": corpus_snapshot["corpus_fingerprint"],
                "results": normalized_results,
            },
        }

    async def delete_dataset(self, dataset_id: str) -> None:
        try:
            row = await self.eval_repo.get_dataset(dataset_id)
            if row is None:
                raise ValueError("Dataset not found")
            await self.eval_repo.delete_dataset(dataset_id)
            logger.info(f"成功删除评估数据集: {dataset_id}")
        except Exception as e:
            logger.error(f"删除评估数据集失败: {e}")
            raise

    async def generate_dataset(
        self,
        kb_id: str,
        name: str,
        description: str,
        count: int,
        neighbors_count: int,
        concurrency_count: int,
        llm_model_spec: str,
        generation_mode: str = "vector",
        graph_expand_top_k: int = 1,
        created_by: str = "system",
    ) -> dict[str, Any]:
        dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
        count = int(count)
        neighbors_count = int(neighbors_count)
        concurrency_count = normalize_generation_concurrency_count(concurrency_count)
        graph_expand_top_k = min(max(1, int(graph_expand_top_k)), 3)
        if generation_mode not in {"vector", "graph_enhanced"}:
            raise ValueError("不支持的评估基准生成方式")
        if generation_mode == "graph_enhanced":
            indexed_count = await self.chunk_repo.count_graph_indexed_by_kb_id(kb_id)
            if indexed_count <= 0:
                raise ValueError("当前知识库尚未完成图索引，无法使用图增强构建")
        build_metadata = {
            "source": "generated",
            "status": "pending",
            "progress": 0,
            "params": {
                "count": count,
                "neighbors_count": neighbors_count,
                "concurrency_count": concurrency_count,
                "llm_model_spec": llm_model_spec,
                "generation_mode": generation_mode,
                "graph_expand_top_k": graph_expand_top_k,
            },
        }
        await self.eval_repo.create_dataset(
            {
                "dataset_id": dataset_id,
                "kb_id": kb_id,
                "name": name,
                "description": description,
                "item_count": 0,
                "has_gold_chunks": True,
                "has_gold_answers": True,
                "build_metadata": build_metadata,
                "created_by": created_by,
            }
        )
        try:
            task = await tasker.enqueue(
                name="生成评估数据集",
                task_type="dataset_generation",
                payload={
                    "dataset_id": dataset_id,
                    "kb_id": kb_id,
                    "created_by": created_by,
                    "name": name,
                    "description": description,
                    "count": count,
                    "neighbors_count": neighbors_count,
                    "concurrency_count": concurrency_count,
                    "llm_model_spec": llm_model_spec,
                    "generation_mode": generation_mode,
                    "graph_expand_top_k": graph_expand_top_k,
                },
                coroutine=self._generate_dataset_task,
            )
        except Exception as exc:
            build_metadata.update(status="failed", progress=100, message=f"任务提交失败: {exc}")
            await self.eval_repo.update_dataset(dataset_id, {"build_metadata": build_metadata})
            raise
        build_metadata["task_id"] = task.id
        await self.eval_repo.update_dataset(dataset_id, {"build_metadata": build_metadata})
        return {"dataset_id": dataset_id, "task_id": task.id, "message": "评估数据集生成任务已提交"}

    async def _update_dataset_build_metadata(
        self, dataset_id: str, metadata: dict[str, Any], **updates
    ) -> dict[str, Any]:
        metadata.update(updates)
        await self.eval_repo.update_dataset(dataset_id, {"build_metadata": metadata})
        return metadata

    async def _generate_dataset_task(self, context: TaskContext):
        await context.set_progress(0, "初始化")
        payload = context.payload

        dataset_id = payload.get("dataset_id")
        kb_id = payload.get("kb_id")
        count = int(payload.get("count", 10))
        neighbors_count = int(payload.get("neighbors_count", 1))
        concurrency_count = normalize_generation_concurrency_count(payload.get("concurrency_count"))
        llm_model_spec = payload.get("llm_model_spec")
        generation_mode = payload.get("generation_mode") or "vector"
        graph_expand_top_k = min(max(1, int(payload.get("graph_expand_top_k", 1))), 3)
        build_metadata = {
            "source": "generated",
            "status": "running",
            "progress": 0,
            "task_id": context.task_id,
            "params": {
                "count": count,
                "neighbors_count": neighbors_count,
                "concurrency_count": concurrency_count,
                "llm_model_spec": llm_model_spec,
                "generation_mode": generation_mode,
                "graph_expand_top_k": graph_expand_top_k,
            },
        }
        await self._update_dataset_build_metadata(dataset_id, build_metadata)

        existing_item_count = await self.eval_repo.count_dataset_items(dataset_id)
        if existing_item_count == count:
            await self.eval_repo.update_dataset(dataset_id, {"item_count": existing_item_count})
            await self._update_dataset_build_metadata(
                dataset_id,
                build_metadata,
                status="completed",
                progress=100,
                message="恢复已完成的数据集生成",
            )
            await context.set_progress(100, "恢复已完成的数据集生成")
            return

        async def report_progress(progress: float, message: str | None = None) -> None:
            await context.set_progress(progress, message)
            await self._update_dataset_build_metadata(
                dataset_id,
                build_metadata,
                progress=max(0, min(round(progress), 100)),
                message=message or build_metadata.get("message", ""),
            )

        try:
            kb_instance = await knowledge_base.aget_kb(kb_id)
            if not kb_instance:
                await report_progress(100, "知识库不存在")
                raise ValueError("Knowledge Base not found")
            if kb_instance.kb_type != "milvus":
                await report_progress(100, "仅支持 commonrag/Milvus 类型知识库生成评估数据集")
                raise ValueError("Unsupported KB type for dataset generation")

            questions = []
            try:
                async for item in iter_generated_benchmark_items(
                    kb_instance=kb_instance,
                    kb_id=kb_id,
                    count=count,
                    neighbors_count=neighbors_count,
                    llm_model_spec=llm_model_spec,
                    concurrency_count=concurrency_count,
                    generation_mode=generation_mode,
                    graph_expand_top_k=graph_expand_top_k,
                    progress_cb=report_progress,
                    cancel_cb=context.raise_if_cancelled,
                ):
                    questions.append(item)
            except ValueError as e:
                if str(e) == "No chunks found in knowledge base":
                    await report_progress(100, "知识库为空或未解析到chunks")
                raise

            if not questions:
                raise ValueError("未生成有效评估题目")

            dataset_items = self._build_dataset_items(dataset_id, kb_id, questions)
            await self.eval_repo.replace_dataset_items(dataset_id, dataset_items)
            await self.eval_repo.update_dataset(dataset_id, {"item_count": len(questions)})
            await self._update_dataset_build_metadata(
                dataset_id,
                build_metadata,
                status="completed",
                progress=100,
                message="完成",
            )
            await context.set_progress(100, "完成")
        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
                if context.cancellation_reason == "shutdown" and not context.is_cancel_requested():
                    await self._update_dataset_build_metadata(
                        dataset_id,
                        build_metadata,
                        status="pending",
                        message="服务重启，任务将继续",
                    )
                    raise
            error = str(e)
            if isinstance(e, asyncio.CancelledError):
                if context.is_cancel_requested():
                    error = "任务已取消"
                elif context.cancellation_reason == "timeout":
                    error = "任务执行超时"
                else:
                    error = "服务停止，任务执行中断"
            await self._update_dataset_build_metadata(
                dataset_id,
                build_metadata,
                status="failed",
                progress=100,
                error_message=error,
                message=error,
            )
            raise

    async def run_evaluation(
        self,
        kb_id: str,
        dataset_id: str,
        name: str | None = None,
        model_config: dict[str, Any] = None,
        created_by: str = "system",
        experiment_id: str | None = None,
        variant_id: str | None = None,
    ) -> str:
        try:
            run_id = f"run_{uuid.uuid4().hex[:8]}"
            run_name = self._normalize_run_name(name, run_id)
            dataset_row = await self.eval_repo.get_dataset(dataset_id)
            if dataset_row is None or dataset_row.kb_id != kb_id:
                raise ValueError("Dataset not found")
            if (dataset_row.build_metadata or {}).get("status", "completed") != "completed":
                raise ValueError("Dataset is not ready")

            retrieval_config = {}
            try:
                kb_row = await self.kb_repo.get_by_kb_id(kb_id)
                query_params = (kb_row.query_params if kb_row else None) or {}
                retrieval_config = query_params.get("options", {}) if isinstance(query_params, dict) else {}
                if not retrieval_config:
                    kb_instance = await knowledge_base.aget_kb(kb_id)
                    if kb_instance:
                        retrieval_config = kb_instance._get_default_query_params(kb_id).get("options", {})
                logger.info(f"从知识库 {kb_id} 加载检索配置: {list(retrieval_config.keys())}")
            except Exception as e:
                logger.error(f"获取知识库检索配置失败: {e}")

            if model_config:
                retrieval_config.update(model_config)

            await self.eval_repo.create_run(
                {
                    "run_id": run_id,
                    "name": run_name,
                    "kb_id": kb_id,
                    "dataset_id": dataset_id,
                    "experiment_id": experiment_id,
                    "variant_id": variant_id,
                    "status": "running",
                    "retrieval_config": retrieval_config,
                    "metrics": {},
                    "overall_score": None,
                    "total_items": dataset_row.item_count or 0,
                    "completed_items": 0,
                    "started_at": utc_now_naive(),
                    "completed_at": None,
                    "created_by": created_by,
                }
            )

            try:
                await tasker.enqueue(
                    name=f"RAG评估({run_name})",
                    task_type="rag_evaluation",
                    payload={
                        "run_id": run_id,
                        "name": run_name,
                        "kb_id": kb_id,
                        "dataset_id": dataset_id,
                        "retrieval_config": retrieval_config,
                        "created_by": created_by,
                        "experiment_id": experiment_id,
                        "variant_id": variant_id,
                    },
                    coroutine=self._run_evaluation_task,
                )
            except Exception as exc:
                await self.eval_repo.update_run(
                    run_id,
                    {
                        "status": "failed",
                        "metrics": {"error": f"任务提交失败: {exc}"},
                        "completed_at": utc_now_naive(),
                    },
                )
                raise
            return run_id
        except Exception as e:
            logger.error(f"启动评估失败: {e}")
            raise

    async def create_experiment(
        self,
        *,
        kb_id: str,
        dataset_id: str,
        name: str,
        description: str,
        baseline_variant_index: int,
        shared_config: dict[str, Any],
        variants: list[dict[str, Any]],
        created_by: str,
    ) -> dict[str, Any]:
        dataset = await self.eval_repo.get_dataset(dataset_id)
        if dataset is None or dataset.kb_id != kb_id:
            raise ValueError("Dataset not found")
        if (dataset.build_metadata or {}).get("status", "completed") != "completed":
            raise ValueError("Dataset is not ready")
        if not 2 <= len(variants) <= EXPERIMENT_VARIANT_LIMIT:
            raise ValueError(f"实验必须包含 2 到 {EXPERIMENT_VARIANT_LIMIT} 个变体")
        if not 0 <= baseline_variant_index < len(variants):
            raise ValueError("基线变体序号无效")

        dataset_items = await self.eval_repo.list_all_dataset_items(dataset_id)
        if not dataset_items:
            raise ValueError("Dataset has no items")
        source_corpus = await self._get_indexed_corpus_snapshot(kb_id)
        dataset_fingerprint = _dataset_fingerprint(dataset_items)
        document_gold_by_item_id = {}
        if dataset.has_gold_chunks:
            document_gold_by_item_id = await self._build_dataset_document_gold(dataset_items)
        if dataset.has_gold_chunks:
            gold_hashes = {hash_value for values in document_gold_by_item_id.values() for hash_value in values}
            if not gold_hashes.issubset(set(source_corpus["content_hashes"])):
                raise ValueError("评估基准的 gold 文档不属于当前源知识库，不能建立受控对比")
        experiment_id = f"experiment_{uuid.uuid4().hex[:12]}"
        variant_rows = []
        for index, variant in enumerate(variants):
            variant_name = str(variant.get("name") or "").strip()
            if not variant_name:
                raise ValueError(f"第 {index + 1} 个实验变体缺少名称")
            execution_type = str(variant.get("execution_type") or "research_compass")
            variant_kb_id = str(variant.get("kb_id") or kb_id).strip()
            config = _normalize_experiment_variant_config(variant.get("retrieval_config") or {})
            if execution_type in {"external_rag", "direct_llm"}:
                external = self._normalize_external_variant(
                    variant=variant,
                    dataset_items=dataset_items,
                    dataset_fingerprint=dataset_fingerprint,
                    corpus_snapshot=source_corpus,
                )
                variant_rows.append(
                    {
                        "variant_id": f"variant_{uuid.uuid4().hex[:12]}",
                        "experiment_id": experiment_id,
                        "variant_index": index,
                        "name": variant_name,
                        "kb_id": kb_id,
                        "dataset_id": dataset_id,
                        "retrieval_config": config,
                        "execution_type": external["execution_type"],
                        "provenance": external["provenance"],
                        "input_snapshot": external["input_snapshot"],
                        "status": "queued",
                    }
                )
                continue
            if execution_type != "research_compass":
                raise ValueError(f"变体 {index + 1} 的执行类型不受支持")
            if variant.get("external_results") or variant.get("provenance"):
                raise ValueError(f"ResearchCompass 变体 {index + 1} 不得携带外部基线数据")
            candidate_corpus = await self._get_indexed_corpus_snapshot(variant_kb_id)
            if candidate_corpus["content_hashes"] != source_corpus["content_hashes"]:
                raise ValueError(
                    f"变体 {index + 1} 的知识库语料与源知识库不一致；"
                    "跨知识库对比要求已索引文档内容哈希集合完全相同"
                )
            if candidate_corpus["embedding_model_spec"] != source_corpus["embedding_model_spec"]:
                raise ValueError(f"变体 {index + 1} 的嵌入模型与源知识库不一致，不能建立受控对比")
            variant_rows.append(
                {
                    "variant_id": f"variant_{uuid.uuid4().hex[:12]}",
                    "experiment_id": experiment_id,
                    "variant_index": index,
                    "name": variant_name,
                    "kb_id": variant_kb_id,
                    "dataset_id": dataset_id,
                    "retrieval_config": config,
                    "execution_type": "research_compass",
                    "provenance": None,
                    "input_snapshot": None,
                    "status": "queued",
                }
            )
        use_document_identity = any(
            row["execution_type"] != "research_compass" or str(row["kb_id"]) != str(kb_id)
            for row in variant_rows
        )

        await self.eval_repo.create_experiment_with_variants(
            {
                "experiment_id": experiment_id,
                "name": name.strip() or f"实验 {experiment_id[-6:]}",
                "description": description.strip(),
                "source_kb_id": kb_id,
                "dataset_id": dataset_id,
                "status": "queued",
                "baseline_variant_id": variant_rows[baseline_variant_index]["variant_id"],
                "dataset_fingerprint": dataset_fingerprint,
                "corpus_snapshot": {
                    "fingerprint": source_corpus["corpus_fingerprint"],
                    "document_count": len(source_corpus["content_hashes"]),
                    "content_hashes": source_corpus["content_hashes"],
                    "embedding_model_spec": source_corpus["embedding_model_spec"],
                    "retrieval_identity": "content_hash" if use_document_identity else "chunk_id",
                },
                "shared_config": dict(shared_config or {}),
                "total_variants": len(variant_rows),
                "completed_variants": 0,
                "created_by": created_by,
            },
            variant_rows,
        )

        async def run(context: TaskContext):
            return await self._run_experiment_task(context, experiment_id=experiment_id)

        try:
            task, created = await tasker.enqueue_unique_by_payload(
                name=f"消融实验 ({name.strip() or experiment_id})",
                task_type="rag_ablation_experiment",
                payload={"experiment_id": experiment_id, "kb_id": kb_id, "dataset_id": dataset_id},
                payload_match={"experiment_id": experiment_id},
                statuses={"pending", "running"},
                coroutine=run,
            )
            if not created:
                raise RuntimeError("实验任务已存在")
        except Exception as exc:
            await self.eval_repo.update_experiment(
                experiment_id,
                {"status": "failed", "error_message": str(exc), "completed_at": utc_now_naive()},
            )
            raise
        await self.eval_repo.update_experiment(experiment_id, {"task_id": task.id})
        return {"experiment_id": experiment_id, "task_id": task.id, "status": "queued"}

    async def _run_experiment_task(self, context: TaskContext, *, experiment_id: str) -> dict[str, Any]:
        experiment = await self.eval_repo.get_experiment(experiment_id)
        if experiment is None:
            raise ValueError("Experiment not found")
        variants = await self.eval_repo.list_experiment_variants(experiment_id)
        if not variants:
            raise ValueError("Experiment has no variants")
        await self.eval_repo.update_experiment(
            experiment_id,
            {"status": "running", "started_at": utc_now_naive(), "error_message": None},
        )
        try:
            total_variants = len(variants)
            for index, variant in enumerate(variants):
                await context.raise_if_cancelled()
                progress_start = (index / total_variants) * 90
                progress_end = ((index + 1) / total_variants) * 90
                await context.set_progress(progress_start, f"运行变体 {index + 1}/{total_variants}：{variant.name}")
                await self.eval_repo.update_experiment_variant(
                    variant.variant_id, {"status": "running", "started_at": utc_now_naive(), "error_message": None}
                )
                try:
                    run_id = await self._run_experiment_variant(
                        context,
                        experiment=experiment,
                        variant=variant,
                        progress_start=progress_start,
                        progress_end=progress_end,
                    )
                    run = await self.eval_repo.get_run(run_id)
                    if run is None or run.status != "completed":
                        message = (run.metrics or {}).get("error") if run is not None else "评估运行记录不存在"
                        raise RuntimeError(str(message or "评估运行失败"))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self.eval_repo.update_experiment_variant(
                        variant.variant_id,
                        {
                            "status": "failed",
                            "error_message": str(exc),
                            "completed_at": utc_now_naive(),
                        },
                    )
                else:
                    await self.eval_repo.update_experiment_variant(
                        variant.variant_id,
                        {
                            "status": "completed",
                            "metrics": run.metrics or {},
                            "overall_score": run.overall_score,
                            "completed_at": utc_now_naive(),
                        },
                    )
                completed = index + 1
                await self.eval_repo.update_experiment(experiment_id, {"completed_variants": completed})

            report = await self._build_experiment_report(experiment_id)
            has_failed_variant = any(item["status"] != "completed" for item in report["variants"])
            final_status = "completed_with_failures" if has_failed_variant else "completed"
            await self.eval_repo.update_experiment(
                experiment_id,
                {
                    "status": final_status,
                    "comparison_report": {**report, "status": final_status},
                    "completed_at": utc_now_naive(),
                },
            )
            await context.set_result(report)
            await context.set_progress(100, "实验对比报告已生成")
            return report
        except (Exception, asyncio.CancelledError) as exc:
            message = "任务已取消" if isinstance(exc, asyncio.CancelledError) else str(exc)
            if isinstance(exc, asyncio.CancelledError) and context.cancellation_reason == "shutdown":
                raise
            await self.eval_repo.update_experiment(
                experiment_id,
                {"status": "failed", "error_message": message, "completed_at": utc_now_naive()},
            )
            raise

    async def _run_experiment_variant(
        self,
        context: TaskContext,
        *,
        experiment: Any,
        variant: Any,
        progress_start: float,
        progress_end: float,
    ) -> str:
        if variant.execution_type != "research_compass":
            return await self._run_external_experiment_variant(
                context,
                experiment=experiment,
                variant=variant,
                progress_start=progress_start,
                progress_end=progress_end,
            )
        retrieval_config = {**(experiment.shared_config or {}), **(variant.retrieval_config or {})}
        await self._ensure_experiment_variant_corpus(experiment, variant)
        evaluation_identity = {
            "field": (experiment.corpus_snapshot or {}).get("retrieval_identity") or "chunk_id",
            "source_kb_id": experiment.source_kb_id,
            "cross_kb": str(variant.kb_id) != str(experiment.source_kb_id),
        }
        if variant.run_id:
            run_id = variant.run_id
            run = await self.eval_repo.get_run(run_id)
            if run is None:
                raise ValueError("实验恢复时找不到评估运行记录")
            if run.status == "running":
                await self._run_evaluation_task(
                    _ExperimentEvaluationContext(
                        context,
                        {
                            "run_id": run_id,
                            "kb_id": variant.kb_id,
                            "dataset_id": variant.dataset_id,
                            "retrieval_config": retrieval_config,
                            "evaluation_identity": evaluation_identity,
                        },
                        progress_start,
                        progress_end,
                    )
                )
            return run_id

        run_id = f"run_{uuid.uuid4().hex[:8]}"
        dataset = await self.eval_repo.get_dataset(variant.dataset_id)
        if dataset is None:
            raise ValueError("Dataset not found")
        await self.eval_repo.create_run(
            {
                "run_id": run_id,
                "name": f"{experiment.name} · {variant.name}",
                "kb_id": variant.kb_id,
                "dataset_id": variant.dataset_id,
                "experiment_id": experiment.experiment_id,
                "variant_id": variant.variant_id,
                "status": "running",
                "retrieval_config": retrieval_config,
                "metrics": {},
                "overall_score": None,
                "total_items": dataset.item_count or 0,
                "completed_items": 0,
                "started_at": utc_now_naive(),
                "created_by": str(experiment.created_by or "system"),
            }
        )
        await self.eval_repo.update_experiment_variant(variant.variant_id, {"run_id": run_id})
        await self._run_evaluation_task(
            _ExperimentEvaluationContext(
                context,
                {
                    "run_id": run_id,
                    "kb_id": variant.kb_id,
                    "dataset_id": variant.dataset_id,
                    "retrieval_config": retrieval_config,
                    "evaluation_identity": evaluation_identity,
                },
                progress_start,
                progress_end,
            )
        )
        return run_id

    async def _run_external_experiment_variant(
        self,
        context: TaskContext,
        *,
        experiment: Any,
        variant: Any,
        progress_start: float,
        progress_end: float,
    ) -> str:
        snapshot = variant.input_snapshot or {}
        if snapshot.get("dataset_fingerprint") != experiment.dataset_fingerprint:
            raise ValueError("外部基线数据集指纹与实验快照不一致")
        if snapshot.get("corpus_fingerprint") != (experiment.corpus_snapshot or {}).get("fingerprint"):
            raise ValueError("外部基线语料指纹与实验快照不一致")
        results = snapshot.get("results")
        if not isinstance(results, list) or not results:
            raise ValueError("外部基线缺少可恢复的逐题结果快照")

        dataset = await self.eval_repo.get_dataset(variant.dataset_id)
        if dataset is None:
            raise ValueError("Dataset not found")
        if variant.execution_type == "direct_llm" and not dataset.has_gold_answers:
            raise ValueError("direct_llm 基线需要带标准答案的评估基准")
        if not dataset.has_gold_chunks and not dataset.has_gold_answers:
            raise ValueError("外部基线评估基准缺少可计算的标准答案或 gold 文档")

        retrieval_identity = (experiment.corpus_snapshot or {}).get("retrieval_identity") or "chunk_id"
        document_gold_by_item_id = {}
        if retrieval_identity == "content_hash" and dataset.has_gold_chunks:
            document_gold_by_item_id = await self._build_dataset_document_gold(
                await self.eval_repo.list_all_dataset_items(variant.dataset_id)
            )
        judge_llm = None
        if dataset.has_gold_answers:
            judge_model_spec = (experiment.shared_config or {}).get("judge_llm")
            if not judge_model_spec:
                raise ValueError("外部基线含答案指标时必须固定 Judge 模型")
            judge_llm = select_model(model_spec=judge_model_spec)

        run_id = variant.run_id
        run = await self.eval_repo.get_run(run_id) if run_id else None
        if run is None:
            run_id = f"run_{uuid.uuid4().hex[:8]}"
            await self.eval_repo.create_run(
                {
                    "run_id": run_id,
                    "name": f"{experiment.name} · {variant.name}",
                    "kb_id": experiment.source_kb_id,
                    "dataset_id": variant.dataset_id,
                    "experiment_id": experiment.experiment_id,
                    "variant_id": variant.variant_id,
                    "status": "running",
                    "retrieval_config": {
                        **(experiment.shared_config or {}),
                        **(variant.retrieval_config or {}),
                        "execution_type": variant.execution_type,
                        "provenance": variant.provenance or {},
                    },
                    "metrics": {},
                    "overall_score": None,
                    "total_items": dataset.item_count or 0,
                    "completed_items": 0,
                    "started_at": utc_now_naive(),
                    "created_by": str(experiment.created_by or "system"),
                }
            )
            await self.eval_repo.update_experiment_variant(variant.variant_id, {"run_id": run_id})

        items = await self.eval_repo.list_all_dataset_items(variant.dataset_id)
        result_by_item_id = {str(item["item_id"]): item for item in results if isinstance(item, dict)}
        if set(result_by_item_id) != {str(item.item_id) for item in items}:
            raise ValueError("外部基线快照与评估基准题目集合不一致")
        retrieval_metrics_list: list[dict[str, float]] = []
        answer_metrics_list: list[dict[str, Any]] = []
        for index, dataset_item in enumerate(items):
            await context.raise_if_cancelled()
            await context.set_progress(
                progress_start + (progress_end - progress_start) * (index / len(items)),
                f"导入外部基线 {index + 1}/{len(items)}",
            )
            result = result_by_item_id[str(dataset_item.item_id)]
            document_hashes = result.get("retrieved_document_hashes") or []
            retrieved_chunks = [
                {"content_hash": value, "metadata": {"content_hash": value}}
                for value in document_hashes
            ]
            gold_hashes = document_gold_by_item_id.get(str(dataset_item.item_id), [])
            retrieval_scores = {}
            if dataset.has_gold_chunks and gold_hashes:
                retrieval_scores = EvaluationMetricsCalculator.calculate_retrieval_metrics(
                    retrieved_chunks,
                    gold_hashes,
                    identity_field="content_hash",
                )
                retrieval_metrics_list.append(retrieval_scores)
            answer_scores = {}
            if dataset.has_gold_answers:
                answer_scores = await EvaluationMetricsCalculator.calculate_answer_metrics(
                    query=dataset_item.query_text,
                    generated_answer=str(result.get("generated_answer") or ""),
                    gold_answer=dataset_item.gold_answer or "",
                    judge_llm=judge_llm,
                )
                answer_metrics_list.append(answer_scores)
            await self.eval_repo.upsert_run_item(
                run_id,
                index,
                {
                    "dataset_item_id": dataset_item.item_id,
                    "query_text": dataset_item.query_text,
                    "gold_chunk_ids": dataset_item.gold_chunk_ids or [],
                    "gold_document_hashes": gold_hashes,
                    "gold_answer": dataset_item.gold_answer,
                    "generated_answer": result.get("generated_answer") or "",
                    "retrieved_chunks": retrieved_chunks,
                    "metrics": {**retrieval_scores, **answer_scores},
                },
            )
            await self.eval_repo.update_run(run_id, {"completed_items": index + 1})

        metrics, overall_score = aggregate_metrics(
            retrieval_metrics_list,
            answer_metrics_list,
            include_overall_score=True,
        )
        await self.eval_repo.update_run(
            run_id,
            {
                "status": "completed",
                "metrics": metrics,
                "overall_score": overall_score,
                "completed_items": len(items),
                "completed_at": utc_now_naive(),
            },
        )
        return run_id

    async def _build_experiment_report(self, experiment_id: str) -> dict[str, Any]:
        experiment = await self.eval_repo.get_experiment(experiment_id)
        if experiment is None:
            raise ValueError("Experiment not found")
        variants = await self.eval_repo.list_experiment_variants(experiment_id)
        baseline = next((item for item in variants if item.variant_id == experiment.baseline_variant_id), None)
        if baseline is None:
            raise ValueError("Experiment baseline variant not found")
        baseline_metrics = {**(baseline.metrics or {})}
        if baseline.overall_score is not None:
            baseline_metrics["overall_score"] = baseline.overall_score
        serialized_variants = []
        for item in variants:
            metrics = {**(item.metrics or {})}
            if item.overall_score is not None:
                metrics["overall_score"] = item.overall_score
            serialized_variants.append(
                {
                    "variant_id": item.variant_id,
                    "kb_id": item.kb_id,
                    "name": item.name,
                    "run_id": item.run_id,
                    "status": item.status,
                    "execution_type": item.execution_type or "research_compass",
                    "provenance": item.provenance or {},
                    "retrieval_config": item.retrieval_config or {},
                    "metrics": metrics,
                    "metric_deltas": {
                        key: _metric_delta(metrics.get(key), baseline_metrics.get(key))
                        for key in _EXPERIMENT_METRIC_ORDER
                        if key in metrics or key in baseline_metrics
                    },
                    "error_message": item.error_message,
                    "started_at": format_utc_datetime(item.started_at),
                    "completed_at": format_utc_datetime(item.completed_at),
                }
            )
        return {
            "experiment_id": experiment.experiment_id,
            "name": experiment.name,
            "status": experiment.status,
            "dataset_id": experiment.dataset_id,
            "dataset_fingerprint": experiment.dataset_fingerprint,
            "corpus_snapshot": experiment.corpus_snapshot or {},
            "baseline_variant_id": baseline.variant_id,
            "shared_config": experiment.shared_config or {},
            "variants": serialized_variants,
        }

    async def list_experiments(self, kb_id: str) -> list[dict[str, Any]]:
        experiments = await self.eval_repo.list_experiments(kb_id)
        return [await self.get_experiment(kb_id, item.experiment_id) for item in experiments]

    async def get_experiment(self, kb_id: str, experiment_id: str) -> dict[str, Any]:
        experiment = await self.eval_repo.get_experiment(experiment_id)
        if experiment is None or experiment.source_kb_id != kb_id:
            raise ValueError("Experiment not found")
        report = await self._build_experiment_report(experiment_id)
        report.update(
            {
                "description": experiment.description or "",
                "task_id": experiment.task_id,
                "total_variants": experiment.total_variants,
                "completed_variants": experiment.completed_variants,
                "error_message": experiment.error_message,
                "created_at": format_utc_datetime(experiment.created_at),
                "started_at": format_utc_datetime(experiment.started_at),
                "completed_at": format_utc_datetime(experiment.completed_at),
            }
        )
        return report

    async def delete_experiment(self, kb_id: str, experiment_id: str) -> None:
        experiment = await self.eval_repo.get_experiment(experiment_id)
        if experiment is None or experiment.source_kb_id != kb_id:
            raise ValueError("Experiment not found")
        if experiment.status in {"queued", "running"}:
            raise ValueError("运行中的实验不能删除")
        await self.eval_repo.delete_experiment(experiment_id)

    async def recover_experiments(self) -> int:
        recovered = 0
        for experiment in await self.eval_repo.list_recoverable_experiments():
            await self.eval_repo.reset_running_experiment_variants(experiment.experiment_id)

            async def run(context: TaskContext, item_id=experiment.experiment_id):
                return await self._run_experiment_task(context, experiment_id=item_id)

            _, created = await tasker.enqueue_unique_by_payload(
                name=f"恢复消融实验 ({experiment.name})",
                task_type="rag_ablation_experiment",
                payload={
                    "experiment_id": experiment.experiment_id,
                    "kb_id": experiment.source_kb_id,
                    "dataset_id": experiment.dataset_id,
                },
                payload_match={"experiment_id": experiment.experiment_id},
                statuses={"pending", "running"},
                coroutine=run,
            )
            recovered += int(created)
        return recovered

    async def _run_evaluation_task(self, context: TaskContext):
        try:
            payload = context.payload

            run_id = payload["run_id"]
            kb_id = payload["kb_id"]
            dataset_id = payload["dataset_id"]
            retrieval_config = payload["retrieval_config"]

            await context.set_progress(5, "加载评估数据集")
            dataset_row = await self.eval_repo.get_dataset(dataset_id)
            evaluation_identity = payload.get("evaluation_identity") or {}
            source_kb_id = str(evaluation_identity.get("source_kb_id") or "")
            is_document_identity = evaluation_identity.get("field") == "content_hash"
            if dataset_row is None or (dataset_row.kb_id != kb_id and dataset_row.kb_id != source_kb_id):
                raise ValueError("Dataset not found")
            if dataset_row.kb_id != kb_id and not is_document_identity:
                raise ValueError("跨知识库评估必须使用源文档身份指标")
            dataset_items = await self.eval_repo.list_all_dataset_items(dataset_id)
            if not dataset_items:
                raise ValueError("Dataset has no items")

            existing_items_by_index = {
                int(item.item_index): item for item in await self.eval_repo.list_all_run_items(run_id)
            }
            await self.eval_repo.update_run(run_id, {"status": "running", "completed_at": None})

            document_gold_by_item_id = {}
            if is_document_identity and dataset_row.has_gold_chunks:
                document_gold_by_item_id = await self._build_dataset_document_gold(dataset_items)

            kb_instance = await knowledge_base.aget_kb(kb_id)
            if not kb_instance:
                raise ValueError(f"Knowledge Base {kb_id} not found")

            judge_llm = None
            if dataset_row.has_gold_answers:
                judge_model_spec = retrieval_config.get("judge_llm") or retrieval_config.get("answer_llm")
                if judge_model_spec:
                    try:
                        logger.debug(f"Initializing Judge LLM: {judge_model_spec}")
                        judge_llm = select_model(model_spec=judge_model_spec)
                    except Exception as e:
                        logger.error(f"Failed to load judge LLM: {e}")

            all_retrieval_metrics = []
            all_answer_metrics = []
            total_items = len(dataset_items)

            async def update_run_db(status=None, completed=None, metrics=None, final_score=None):
                data = {}
                if status is not None:
                    data["status"] = status
                    if status in ["completed", "failed"]:
                        data["completed_at"] = utc_now_naive()
                if completed is not None:
                    data["completed_items"] = completed
                if metrics is not None:
                    data["metrics"] = metrics
                if final_score is not None:
                    data["overall_score"] = final_score
                if data:
                    await self.eval_repo.update_run(run_id, data)

            for index, item in enumerate(dataset_items):
                await context.raise_if_cancelled()
                progress = 10 + (index / total_items) * 80
                await context.set_progress(progress, f"评估 {index + 1}/{total_items}")

                question_data = {
                    "query": item.query_text,
                    "gold_chunk_ids": item.gold_chunk_ids or [],
                    "gold_document_hashes": document_gold_by_item_id.get(str(item.item_id), []),
                    "gold_answer": item.gold_answer,
                }
                retrieval_gold_ids = (
                    question_data["gold_document_hashes"] if is_document_identity else question_data["gold_chunk_ids"]
                )
                existing_item = existing_items_by_index.get(index)
                if existing_item is not None and str(existing_item.dataset_item_id) == str(item.item_id):
                    existing_metrics = existing_item.metrics or {}
                    if dataset_row.has_gold_chunks and retrieval_gold_ids:
                        all_retrieval_metrics.append(
                            {
                                key: value
                                for key, value in existing_metrics.items()
                                if key.startswith(("recall@", "f1@"))
                            }
                        )
                    if dataset_row.has_gold_answers and question_data.get("gold_answer") and judge_llm:
                        if "score" in existing_metrics:
                            all_answer_metrics.append(
                                {key: value for key, value in existing_metrics.items() if key in {"score", "reasoning"}}
                            )
                    if (index + 1) % 5 == 0 or (index + 1) == total_items:
                        current_metrics, _ = aggregate_metrics(all_retrieval_metrics, all_answer_metrics)
                        await context.set_result(
                            {
                                "current_metrics": current_metrics,
                                "completed_items": index + 1,
                                "total_items": total_items,
                            }
                        )
                        await update_run_db(completed=index + 1)
                    continue
                question_result = await evaluate_question(
                    kb_instance=kb_instance,
                    kb_id=kb_id,
                    question_data=question_data,
                    retrieval_config=retrieval_config,
                    has_gold_chunks=dataset_row.has_gold_chunks,
                    has_gold_answers=dataset_row.has_gold_answers,
                    judge_llm=judge_llm,
                    select_model_fn=select_model,
                    retrieval_identity_field="content_hash" if is_document_identity else "chunk_id",
                    gold_retrieval_ids=question_data["gold_document_hashes"] if is_document_identity else None,
                )
                if dataset_row.has_gold_chunks and retrieval_gold_ids:
                    all_retrieval_metrics.append(question_result["retrieval_scores"])
                if dataset_row.has_gold_answers and question_data.get("gold_answer") and judge_llm:
                    all_answer_metrics.append(question_result["answer_scores"])

                await self.eval_repo.upsert_run_item(
                    run_id=run_id,
                    item_index=index,
                    data={"dataset_item_id": item.item_id, **question_result["detail"]},
                )

                if (index + 1) % 5 == 0 or (index + 1) == total_items:
                    current_metrics, _ = aggregate_metrics(all_retrieval_metrics, all_answer_metrics)
                    await context.set_result(
                        {"current_metrics": current_metrics, "completed_items": index + 1, "total_items": total_items}
                    )
                    await update_run_db(completed=index + 1)

            await context.set_progress(95, "计算最终指标")
            overall_metrics, overall_score = aggregate_metrics(
                all_retrieval_metrics, all_answer_metrics, include_overall_score=True
            )
            await update_run_db(
                status="completed",
                completed=total_items,
                metrics=overall_metrics,
                final_score=overall_score,
            )
            await context.set_progress(100, "完成")
        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
                if context.cancellation_reason == "shutdown" and not context.is_cancel_requested():
                    if "payload" in locals():
                        await self.eval_repo.update_run(payload["run_id"], {"status": "running", "completed_at": None})
                    raise
            error = str(e)
            if isinstance(e, asyncio.CancelledError):
                if context.is_cancel_requested():
                    error = "任务已取消"
                elif context.cancellation_reason == "timeout":
                    error = "任务执行超时"
                else:
                    error = "服务停止，任务执行中断"
            logger.error(f"Task failed: {error}")
            try:
                if "payload" in locals():
                    await self.eval_repo.update_run(
                        payload["run_id"],
                        {"status": "failed", "metrics": {"error": error}, "completed_at": utc_now_naive()},
                    )
            except Exception as exc:
                logger.error(f"Error updating run record: {exc}")
            await context.set_message(f"Error: {error}")
            raise

    async def list_runs(self, kb_id: str) -> list[dict[str, Any]]:
        try:
            rows = await self.eval_repo.list_runs(kb_id)
            running_run_ids = {row.run_id for row in rows if row.status == "running"}
            task_by_run_id = {}
            if running_run_ids:
                tasks = await self.task_repo.list_all()
                task_by_run_id = {
                    (task.payload or {}).get("run_id"): task
                    for task in tasks
                    if task.type == "rag_evaluation"
                    and task.status in {"pending", "running"}
                    and (task.payload or {}).get("run_id") in running_run_ids
                }

            runs = []
            for row in rows:
                run = {
                    "run_id": row.run_id,
                    "name": self._run_name_from_row(row),
                    "dataset_id": row.dataset_id,
                    "status": row.status,
                    "started_at": format_utc_datetime(row.started_at),
                    "completed_at": format_utc_datetime(row.completed_at),
                    "total_items": row.total_items,
                    "completed_items": row.completed_items,
                    "overall_score": row.overall_score,
                    "retrieval_config": row.retrieval_config or {},
                    "metrics": row.metrics or {},
                }
                if row.status == "running":
                    task = task_by_run_id.get(row.run_id)
                    if task:
                        run.update(progress=task.progress, message=task.message)
                runs.append(run)
            return runs
        except Exception as e:
            logger.error(f"获取评估运行历史失败: {e}")
            raise

    async def get_run_results(
        self, kb_id: str, run_id: str, page: int = 1, page_size: int = 20, error_only: bool = False
    ) -> dict[str, Any]:
        if not re.match(r"^run_[a-f0-9]{8}$", run_id):
            raise ValueError("Invalid run_id format")
        row = await self.eval_repo.get_run(run_id)
        if row is None or row.kb_id != kb_id:
            task = await tasker.get_task(run_id)
            if task:
                return {"run_id": run_id, "status": task.status, "progress": task.progress, "message": task.message}
            raise ValueError(f"Run not found for {run_id}")

        start_idx = (page - 1) * page_size
        if error_only:
            total = 0
            paged_items = []
            offset = 0
            batch_size = 200
            while True:
                batch = await self.eval_repo.list_run_items(run_id, offset, batch_size)
                if not batch:
                    break
                for item in batch:
                    if not self._is_error_run_item(item):
                        continue
                    if start_idx <= total < start_idx + page_size:
                        paged_items.append(self._run_item_to_dict(item))
                    total += 1
                offset += batch_size
        else:
            total = await self.eval_repo.count_run_items(run_id)
            details = await self.eval_repo.list_run_items(run_id, start_idx, page_size)
            paged_items = [self._run_item_to_dict(item) for item in details]
        return {
            "run_id": row.run_id,
            "name": self._run_name_from_row(row),
            "status": row.status,
            "started_at": format_utc_datetime(row.started_at),
            "completed_at": format_utc_datetime(row.completed_at),
            "total_items": row.total_items or 0,
            "completed_items": row.completed_items or 0,
            "overall_score": row.overall_score,
            "retrieval_config": row.retrieval_config or {},
            "items": paged_items,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
                "error_only": error_only,
            },
        }

    async def delete_run(self, kb_id: str, run_id: str) -> None:
        if not re.match(r"^run_[a-f0-9]{8}$", run_id):
            raise ValueError("Invalid run_id format")
        row = await self.eval_repo.get_run(run_id)
        if row is None or row.kb_id != kb_id:
            raise ValueError("Run not found")
        await self.eval_repo.delete_run(run_id)
        logger.info(f"成功删除评估运行: {run_id}")


async def _resume_dataset_generation_task(context: TaskContext):
    return await EvaluationService()._generate_dataset_task(context)


async def _resume_rag_evaluation_task(context: TaskContext):
    return await EvaluationService()._run_evaluation_task(context)


tasker.register_resumable_handler("dataset_generation", _resume_dataset_generation_task)
tasker.register_resumable_handler("rag_evaluation", _resume_rag_evaluation_task)
