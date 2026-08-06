"""ResearchCompass 科研论文检索服务。

本模块是本仓库作者在开源智能体框架 Yuxi 之上设计的科研检索业务：提供"本地混合
检索"与"严格图谱混合检索"两种模式，串联查询改写、混合召回、重排序、证据聚合、以及
基于学术引用图谱的 PPR 扩展与融合排序。混合召回、重排序与图谱运行时由 Yuxi 的
知识库运行时与 AcademicGraphService 提供；本模块定义科研检索的阶段编排、严格图谱
模式的前置约束、证据投影与运行记录语义。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from yuxi.config import config
from yuxi.knowledge.graphs.academic_graph_service import AcademicGraphService
from yuxi.knowledge.runtime import knowledge_base
from yuxi.models import select_model
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.academic_graph_repository import AcademicGraphRepository
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.research_search_run_repository import ResearchSearchRunRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_now_naive


class ResearchSearchError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


LOCAL_HYBRID_MODE = "local_hybrid"
STRICT_HYBRID_GRAPH_MODE = "strict_hybrid_citation_graph"
DEFAULT_RESEARCH_SEARCH_MODE = STRICT_HYBRID_GRAPH_MODE
RESEARCH_SEARCH_MODES = {LOCAL_HYBRID_MODE, STRICT_HYBRID_GRAPH_MODE}


def _normalize_search_mode(value: str | None) -> str:
    mode = str(value or DEFAULT_RESEARCH_SEARCH_MODE).strip()
    if mode not in RESEARCH_SEARCH_MODES:
        raise ResearchSearchError("invalid_retrieval_config", f"不支持的科研检索模式: {mode}")
    return mode


def _elapsed(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def _parse_rewrite(content: str) -> dict[str, Any]:
    value = content.strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ResearchSearchError("query_rewrite_invalid", "查询改写模型返回了不可解析的 JSON") from exc
    if not isinstance(payload, dict):
        raise ResearchSearchError("query_rewrite_invalid", "查询改写结果必须是 JSON 对象")
    rewritten = payload.get("rewritten_query")
    keywords = payload.get("keywords")
    if not isinstance(rewritten, str) or not rewritten.strip():
        raise ResearchSearchError("query_rewrite_invalid", "查询改写结果缺少 rewritten_query")
    if not isinstance(keywords, list) or any(not isinstance(item, str) or not item.strip() for item in keywords):
        raise ResearchSearchError("query_rewrite_invalid", "查询改写结果缺少有效 keywords 数组")
    return {
        "rewritten_query": rewritten.strip(),
        "keywords": [item.strip() for item in keywords[:20]],
    }


def _public_model_config(chat_model: str, reranker_model: str) -> dict[str, str]:
    chat_info = model_cache.get_model_info(chat_model)
    reranker_info = model_cache.get_model_info(reranker_model)
    if chat_info is None or chat_info.model_type != "chat":
        raise ResearchSearchError("chat_model_unavailable", "科研检索聊天模型未配置或类型不正确")
    if not chat_info.api_key:
        raise ResearchSearchError("chat_model_unavailable", "科研检索聊天模型缺少 API Key")
    if reranker_info is None or reranker_info.model_type != "rerank":
        raise ResearchSearchError("reranker_unavailable", "科研检索重排模型未配置或类型不正确")
    if not reranker_info.api_key:
        raise ResearchSearchError("reranker_unavailable", "科研检索重排模型缺少 API Key")
    return {
        "chat_model": chat_model,
        "reranker_model": reranker_model,
        "chat_provider": chat_info.provider_type,
        "reranker_provider": reranker_info.provider_type,
    }


async def _rewrite_query(query: str, model_spec: str) -> dict[str, Any]:
    model = select_model(model_spec=model_spec, model_params={"temperature": 0})
    response = await model.call(
        [
            {
                "role": "system",
                "content": (
                    "你是学术检索查询改写器。只返回合法 JSON，不要 Markdown。"
                    'JSON 格式必须是 {"rewritten_query":"...", "keywords":["..."]}。'
                    "保留研究对象、方法、任务、数据集和时间范围等关键约束，不能编造事实。"
                ),
            },
            {"role": "user", "content": query},
        ],
        stream=False,
    )
    return _parse_rewrite(str(response.content or ""))


def _aggregate_results(kb_id: str, chunks: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    papers: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        paper = metadata.get("paper") if isinstance(metadata.get("paper"), dict) else {}
        paper_id = str(paper.get("paper_id") or metadata.get("paper_id") or "")
        if not paper_id:
            raise ResearchSearchError("evidence_missing_paper", "检索结果缺少论文身份元数据")
        entry = papers.setdefault(
            paper_id,
            {
                "paper_id": paper_id,
                "title": paper.get("title") or "未命名论文",
                "authors": paper.get("authors") or [],
                "publication_year": paper.get("publication_year"),
                "venue": paper.get("venue"),
                "doi": paper.get("doi"),
                "citation_count": paper.get("citation_count"),
                "scores": {
                    "vector": None,
                    "bm25": None,
                    "hybrid": None,
                    "rerank": None,
                    "graph": None,
                    "fusion": None,
                },
                "evidence": [],
            },
        )
        scores = entry["scores"]
        for field, key in (
            ("vector", "vector_score"),
            ("bm25", "bm25_score"),
            ("hybrid", "hybrid_score"),
            ("rerank", "rerank_score"),
            ("graph", "graph_score"),
            ("fusion", "fusion_score"),
        ):
            value = chunk.get(key)
            if value is not None:
                scores[field] = max(float(value), float(scores[field] or 0.0))
        entry["evidence"].append(
            {
                "chunk_id": metadata.get("chunk_id"),
                "file_id": metadata.get("file_id"),
                "chunk_index": metadata.get("chunk_index"),
                "content": chunk.get("content"),
                "section_type": metadata.get("section_type"),
                "section_title": metadata.get("section_title"),
                "section_path": metadata.get("section_path") or [],
                "element_types": metadata.get("element_types") or [],
                "start_char_pos": metadata.get("source_start", metadata.get("start_char_pos")),
                "end_char_pos": metadata.get("source_end", metadata.get("end_char_pos")),
                "source_page_start": metadata.get("source_page_start"),
                "source_page_end": metadata.get("source_page_end"),
                "source_rects": metadata.get("source_rects") or [],
                "locator_type": metadata.get("locator_type") or "parsed_source_character_range",
                "scores": {
                    key: float(chunk[field])
                    for key, field in (
                        ("vector", "vector_score"),
                        ("bm25", "bm25_score"),
                        ("hybrid", "hybrid_score"),
                        ("rerank", "rerank_score"),
                        ("graph", "graph_score"),
                        ("fusion", "fusion_score"),
                    )
                    if chunk.get(field) is not None
                },
            }
        )

    for entry in papers.values():
        for evidence in entry["evidence"]:
            evidence["locator"] = _evidence_locator(
                kb_id=kb_id,
                paper_id=entry["paper_id"],
                evidence=evidence,
            )
        entry["evidence"].sort(
            key=lambda item: max(item["scores"].values() or [0.0]),
            reverse=True,
        )
        entry["evidence"] = entry["evidence"][:10]
        entry["ranking_score"] = max(
            float(entry["scores"].get("rerank") or 0.0),
            float(entry["scores"].get("fusion") or 0.0),
            float(entry["scores"].get("hybrid") or 0.0),
        )
    return sorted(papers.values(), key=lambda item: item["ranking_score"], reverse=True)[:top_k]


def _evidence_locator(*, kb_id: str, paper_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    chunk_id = evidence.get("chunk_id")
    if not chunk_id:
        raise ResearchSearchError("evidence_missing_locator", "检索证据缺少 chunk_id 定位信息")
    return {
        "api_path": f"/api/research/databases/{kb_id}/papers/{paper_id}/evidence/{chunk_id}",
        "chunk_api_path": f"/api/research/databases/{kb_id}/papers/{paper_id}/chunks?chunk_id={chunk_id}",
        "file_id": evidence.get("file_id"),
        "chunk_id": chunk_id,
        "section_type": evidence.get("section_type"),
        "section_title": evidence.get("section_title"),
        "start_char_pos": evidence.get("start_char_pos"),
        "end_char_pos": evidence.get("end_char_pos"),
        "source_page_start": evidence.get("source_page_start"),
        "source_page_end": evidence.get("source_page_end"),
        "source_rects": evidence.get("source_rects") or [],
        "locator_type": evidence.get("locator_type") or "parsed_source_character_range",
    }


async def _hydrate_paper_cards(kb_id: str, results: list[dict[str, Any]]) -> None:
    file_ids = [str(item["evidence"][0]["file_id"]) for item in results if item.get("evidence")]
    records = await AcademicPaperRepository().list_by_file_ids(kb_id=kb_id, file_ids=file_ids)
    records_by_file = {str(record.file_id): record for record in records}
    for item in results:
        evidence = item.get("evidence") or []
        record = records_by_file.get(str(evidence[0].get("file_id"))) if evidence else None
        if record is None:
            continue
        item.update(
            {
                "paper_id": record.paper_id,
                "title": record.title,
                "abstract": record.abstract,
                "authors": record.authors or [],
                "publication_year": record.publication_year,
                "venue": record.venue,
                "doi": record.doi,
                "keywords": record.keywords or [],
                "citation_count": record.citation_count,
                "metadata_status": record.metadata_status,
            }
        )


async def _apply_citation_graph(
    *,
    kb_id: str,
    results: list[dict[str, Any]],
    top_k: int,
    recall_top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not results:
        return [], {"items": [], "seed_count": 0, "expanded_count": 0, "node_count": 0, "citation_count": 0}

    paper_scores = {str(item["paper_id"]): float(item["ranking_score"]) for item in results}
    graph_repo = AcademicGraphRepository()
    seed_weights = await graph_repo.resolve_seed_papers(kb_id=kb_id, paper_scores=paper_scores)
    if len(seed_weights) != len(paper_scores):
        raise ResearchSearchError(
            "citation_graph_seed_missing",
            "部分检索命中论文尚未同步到学术引用图谱",
        )
    if sum(max(score, 0.0) for score in seed_weights.values()) <= 0:
        raise ResearchSearchError("citation_graph_seed_invalid", "论文引用图谱种子权重必须大于零")

    try:
        expansion = await AcademicGraphService().expand_papers_by_ppr(
            kb_id=kb_id,
            seed_weights=seed_weights,
            depth=2,
            max_nodes=2000,
            top_k=min(max(recall_top_k * 2, top_k), 200),
            damping=0.85,
        )
    except Exception as exc:
        raise ResearchSearchError("citation_graph_failure", "论文引用图谱 PPR 扩展失败") from exc

    ranked_graph_papers = expansion["papers"]
    mapped_graph_paper_ids = list(seed_weights)
    mapped_graph_paper_ids.extend(
        item["graph_paper_id"] for item in ranked_graph_papers if item["graph_paper_id"] not in seed_weights
    )
    local_paper_ids = await graph_repo.list_library_paper_ids(
        kb_id=kb_id,
        graph_paper_ids=mapped_graph_paper_ids,
    )
    graph_rank = {
        graph_paper_id: rank
        for rank, (graph_paper_id, _) in enumerate(
            sorted(expansion["paper_scores"].items(), key=lambda item: (-item[1], item[0])),
            start=1,
        )
    }
    graph_id_by_paper_id = {paper_id: graph_id for graph_id, paper_id in local_paper_ids.items()}
    for text_rank, item in enumerate(results, start=1):
        graph_paper_id = graph_id_by_paper_id[str(item["paper_id"])]
        item["scores"]["graph"] = float(expansion["paper_scores"][graph_paper_id])
        item["scores"]["fusion"] = 1.0 / (60 + text_rank) + 1.0 / (60 + graph_rank[graph_paper_id])
        item["ranking_score"] = item["scores"]["fusion"]
    results.sort(key=lambda item: (-item["ranking_score"], item["paper_id"]))

    expanded_items = []
    for item in ranked_graph_papers:
        if item["is_seed"]:
            continue
        graph_paper_id = item["graph_paper_id"]
        expanded_items.append(
            {
                "graph_paper_id": graph_paper_id,
                "paper_id": local_paper_ids.get(graph_paper_id),
                "title": item.get("title"),
                "publication_year": item.get("publication_year"),
                "venue": item.get("venue"),
                "citation_count": item.get("citation_count"),
                "is_library_paper": bool(item.get("is_library_paper")),
                "graph_score": item["graph_score"],
                "path": item["path"],
            }
        )
    return results[:top_k], {
        "items": expanded_items,
        "seed_count": len(seed_weights),
        "expanded_count": len(expanded_items),
        "node_count": expansion["node_count"],
        "citation_count": expansion["citation_count"],
    }


async def search_papers(
    *,
    kb_id: str,
    current_user: User,
    query: str,
    top_k: int,
    recall_top_k: int,
    year_from: int | None,
    year_to: int | None,
    chat_model: str | None,
    reranker_model: str | None,
    retrieval_mode: str | None = None,
    parent_run_id: str | None = None,
    retry_count: int = 0,
) -> dict[str, Any]:
    query = query.strip()
    if not query or len(query) > 4000:
        raise ResearchSearchError("invalid_query", "查询长度必须在 1 到 4000 个字符之间")
    if year_from is not None and year_to is not None and year_from > year_to:
        raise ResearchSearchError("invalid_filter", "year_from 不能大于 year_to")
    if top_k < 1 or top_k > 50 or recall_top_k < top_k or recall_top_k > 200:
        raise ResearchSearchError("invalid_retrieval_config", "top_k 或 recall_top_k 超出允许范围")
    retrieval_mode = _normalize_search_mode(retrieval_mode)
    citation_graph_enabled = retrieval_mode == STRICT_HYBRID_GRAPH_MODE

    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise ResearchSearchError("knowledge_base_not_found", "知识库不存在")
    user_info = {"uid": str(current_user.uid), "role": current_user.role, "department_id": current_user.department_id}
    if not await knowledge_base.check_accessible(user_info, kb_id):
        raise ResearchSearchError("forbidden", "无权访问该知识库")
    if (kb.kb_type or "milvus").lower() != "milvus":
        raise ResearchSearchError("unsupported_knowledge_base", "科研检索只支持 Milvus 学术知识库")

    chat_model = (chat_model or kb.llm_model_spec or "").strip()
    reranker_model = (reranker_model or config.reranker or "").strip()
    run_id = uuid.uuid4().hex
    run_repo = ResearchSearchRunRepository()
    retrieval_config = {
        "mode": retrieval_mode,
        "top_k": top_k,
        "recall_top_k": recall_top_k,
        "year_from": year_from,
        "year_to": year_to,
        "bm25": True,
        "reranker": True,
        "citation_graph": citation_graph_enabled,
    }
    if citation_graph_enabled:
        retrieval_config.update(
            {
                "graph": "academic_citation_ppr_undirected",
                "graph_depth": 2,
                "graph_max_nodes": 2000,
                "ppr_damping": 0.85,
            }
        )
    await run_repo.create(
        run_id=run_id,
        parent_run_id=parent_run_id,
        retry_count=retry_count,
        kb_id=kb_id,
        uid=str(current_user.uid),
        raw_query=query,
        model_config={"chat_model": chat_model, "reranker_model": reranker_model},
        retrieval_config=retrieval_config,
    )
    timings: dict[str, int] = {}
    try:
        public_models = _public_model_config(chat_model, reranker_model)
        await run_repo.update(run_id, {"model_config_json": public_models})

        graph_status = None
        if citation_graph_enabled:
            started = time.perf_counter()
            try:
                graph_status = await AcademicGraphService().get_status(kb_id=kb_id)
            except Exception as exc:
                raise ResearchSearchError("graph_not_ready", "无法读取论文引用图谱状态") from exc
            timings["graph_preflight_ms"] = _elapsed(started)
            await run_repo.update(run_id, {"stage_timings": timings})
            if graph_status["papers"] <= 0 or graph_status["citations"] <= 0:
                raise ResearchSearchError("graph_not_ready", "科研严格检索要求学术引用图谱已同步且包含引用关系")

        started = time.perf_counter()
        rewrite = await _rewrite_query(query, chat_model)
        timings["query_rewrite_ms"] = _elapsed(started)
        await run_repo.update(
            run_id,
            {
                "rewritten_query": rewrite["rewritten_query"],
                "rewrite_keywords": rewrite["keywords"],
                "stage_timings": timings,
            },
        )

        file_ids = await AcademicPaperRepository().list_file_ids_by_filters(
            kb_id=kb_id, year_from=year_from, year_to=year_to
        )
        if not file_ids:
            timings["retrieval_ms"] = 0
            timings["aggregation_ms"] = 0
            response = {
                "run_id": run_id,
                "query": query,
                "rewritten_query": rewrite["rewritten_query"],
                "keywords": rewrite["keywords"],
                "items": [],
                "total": 0,
                "graph_expansion": {
                    "items": [],
                    "seed_count": 0,
                    "expanded_count": 0,
                    "node_count": 0,
                    "citation_count": 0,
                }
                if citation_graph_enabled
                else None,
                "config": {
                    **retrieval_config,
                    **public_models,
                    **({"graph_status": graph_status} if graph_status is not None else {}),
                },
                "stage_timings": timings,
            }
            await run_repo.update(
                run_id,
                {
                    "status": "success",
                    "stage_timings": timings,
                    "result_count": 0,
                    "result_snapshot": response,
                    "completed_at": utc_now_naive(),
                },
            )
            return response
        started = time.perf_counter()
        try:
            chunks = await knowledge_base.aquery(
                rewrite["rewritten_query"],
                kb_id,
                strict_research=True,
                search_mode="hybrid",
                recall_top_k=recall_top_k,
                final_top_k=recall_top_k,
                use_reranker=True,
                reranker_model=reranker_model,
                use_graph_retrieval=False,
                file_ids=file_ids,
                include_distances=True,
                similarity_threshold=0.0,
            )
        except Exception as exc:
            raise ResearchSearchError("retrieval_failure", "混合召回或重排序阶段失败") from exc
        timings["retrieval_ms"] = _elapsed(started)
        started = time.perf_counter()
        results = _aggregate_results(kb_id, chunks, recall_top_k)
        await _hydrate_paper_cards(kb_id, results)
        timings["aggregation_ms"] = _elapsed(started)
        graph_expansion = None
        if citation_graph_enabled:
            started = time.perf_counter()
            results, graph_expansion = await _apply_citation_graph(
                kb_id=kb_id,
                results=results,
                top_k=top_k,
                recall_top_k=recall_top_k,
            )
            timings["citation_graph_ms"] = _elapsed(started)
        else:
            results = results[:top_k]
        response = {
            "run_id": run_id,
            "query": query,
            "rewritten_query": rewrite["rewritten_query"],
            "keywords": rewrite["keywords"],
            "items": results,
            "total": len(results),
            "graph_expansion": graph_expansion,
            "config": {
                **retrieval_config,
                **public_models,
                **({"graph_status": graph_status} if graph_status is not None else {}),
            },
            "stage_timings": timings,
        }
        await run_repo.update(
            run_id,
            {
                "status": "success",
                "stage_timings": timings,
                "result_count": len(results),
                "result_snapshot": response,
                "completed_at": utc_now_naive(),
            },
        )
        return response
    except ResearchSearchError as exc:
        retryable = exc.error_type == "retrieval_failure"
        dependency = "vector_store" if exc.error_type == "retrieval_failure" else None
        await run_repo.update(
            run_id,
            {
                "status": "failed",
                "stage_timings": timings,
                "error_type": exc.error_type,
                "error_message": exc.message,
                "retryable": retryable,
                "dependency": dependency,
                "completed_at": utc_now_naive(),
            },
        )
        raise
    except Exception as exc:
        failure_type = "strict_research_failure" if citation_graph_enabled else "local_research_failure"
        failure_message = "科研严格图谱检索执行失败" if citation_graph_enabled else "科研本地混合检索执行失败"
        dependency = "vector_store" if not citation_graph_enabled else "retrieval"
        await run_repo.update(
            run_id,
            {
                "status": "failed",
                "stage_timings": timings,
                "error_type": failure_type,
                "error_message": failure_message,
                "retryable": True,
                "dependency": dependency,
                "completed_at": utc_now_naive(),
            },
        )
        raise ResearchSearchError(failure_type, failure_message) from exc


async def get_search_run(*, run_id: str, current_user: User) -> dict[str, Any]:
    repository = ResearchSearchRunRepository()
    record = await repository.get(run_id, uid=str(current_user.uid))
    if record is None:
        raise ResearchSearchError("run_not_found", "检索运行记录不存在")
    await _ensure_access(current_user, str(record.kb_id))
    return repository.serialize(record, include_result=True)


async def list_search_runs(
    *,
    kb_id: str,
    current_user: User,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    repository = ResearchSearchRunRepository()
    records, total = await repository.list_for_user(
        kb_id=kb_id,
        uid=str(current_user.uid),
        offset=offset,
        limit=limit,
    )
    return {
        "items": [repository.serialize(record) for record in records],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(records) < total,
    }


async def set_search_run_pinned(*, run_id: str, current_user: User, is_pinned: bool) -> dict[str, Any]:
    repository = ResearchSearchRunRepository()
    record = await repository.get(run_id, uid=str(current_user.uid))
    if record is None:
        raise ResearchSearchError("run_not_found", "检索运行记录不存在")
    await _ensure_access(current_user, str(record.kb_id))
    updated = await repository.update(run_id, {"is_pinned": is_pinned})
    if updated is None:
        raise ResearchSearchError("run_not_found", "检索运行记录不存在")
    return repository.serialize(updated)


MAX_RESEARCH_SEARCH_RETRIES = 3


async def retry_search_run(*, run_id: str, current_user: User) -> dict[str, Any]:
    repository = ResearchSearchRunRepository()
    record = await repository.get(run_id, uid=str(current_user.uid))
    if record is None:
        raise ResearchSearchError("run_not_found", "检索运行记录不存在")
    await _ensure_access(current_user, str(record.kb_id))
    if record.status != "failed" or not bool(record.retryable):
        raise ResearchSearchError("search_not_retryable", "该检索不是可重试失败状态")
    retry_count = int(record.retry_count or 0)
    if retry_count >= MAX_RESEARCH_SEARCH_RETRIES:
        raise ResearchSearchError("search_retry_limit", "检索重试次数已达上限")
    claimed_retry_count = await repository.claim_retry(
        str(record.run_id), uid=str(current_user.uid), max_retries=MAX_RESEARCH_SEARCH_RETRIES
    )
    if claimed_retry_count is None:
        raise ResearchSearchError("search_retry_limit", "检索重试次数已达上限或运行状态已改变")
    retrieval = record.retrieval_config or {}
    models = record.model_config_json or {}
    result = await search_papers(
        kb_id=str(record.kb_id), current_user=current_user, query=str(record.raw_query),
        top_k=int(retrieval.get("top_k") or 8), recall_top_k=int(retrieval.get("recall_top_k") or 50),
        year_from=retrieval.get("year_from"), year_to=retrieval.get("year_to"),
        chat_model=str(models.get("chat_model") or ""), reranker_model=str(models.get("reranker_model") or ""),
        retrieval_mode=str(retrieval.get("mode") or LOCAL_HYBRID_MODE), parent_run_id=str(record.run_id),
        retry_count=claimed_retry_count,
    )
    result["retry_count"] = claimed_retry_count
    return result


async def delete_search_run(*, run_id: str, current_user: User) -> None:
    repository = ResearchSearchRunRepository()
    record = await repository.get(run_id, uid=str(current_user.uid))
    if record is None:
        raise ResearchSearchError("run_not_found", "检索运行记录不存在")
    await _ensure_access(current_user, str(record.kb_id))
    if record.status == "running":
        raise ResearchSearchError("run_active", "运行中的检索不能删除")
    if not await repository.delete(run_id, uid=str(current_user.uid)):
        raise ResearchSearchError("run_not_found", "检索运行记录不存在")


__all__ = [
    "DEFAULT_RESEARCH_SEARCH_MODE",
    "LOCAL_HYBRID_MODE",
    "STRICT_HYBRID_GRAPH_MODE",
    "ResearchSearchError",
    "delete_search_run",
    "get_search_run",
    "list_search_runs",
    "search_papers",
    "retry_search_run",
    "set_search_run_pinned",
]
