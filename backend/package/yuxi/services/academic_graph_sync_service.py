"""ResearchCompass 学术引用图谱同步服务。

本模块是本仓库作者在开源智能体框架 Yuxi 之上实现的学术图谱同步业务：把知识库内
论文与 Semantic Scholar 对齐，拉取引用/参考文献边，写入图数据库与关系库，并记录
身份冲突与可恢复的同步检查点。Semantic Scholar 客户端、Neo4j 图服务、任务调度
与持久化由 Yuxi 提供；本模块定义图谱身份键、引用边投影、冲突记录与同步运行的状态
机。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import HTTPException
from yuxi.knowledge.graphs.academic_graph_service import AcademicGraphService
from yuxi.repositories.academic_graph_repository import AcademicGraphRepository
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.services.semantic_scholar_service import (
    SemanticScholarClient,
    SemanticScholarError,
    normalize_paper_title,
)
from yuxi.services.task_service import PublicTaskError, TaskContext, tasker
from yuxi.storage.postgres.models_business import User
from yuxi.utils import hashstr, logger
from yuxi.utils.datetime_utils import utc_now_naive


class AcademicGraphSyncError(PublicTaskError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _identity_key(paper: dict[str, Any]) -> str:
    external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    doi = str(external_ids.get("DOI") or "").strip().casefold()
    if doi:
        return f"doi:{doi}"
    semantic_scholar_id = str(paper.get("paperId") or "").strip()
    if semantic_scholar_id:
        return f"s2:{semantic_scholar_id}"
    title = normalize_paper_title(str(paper.get("title") or ""))
    year = paper.get("year") or ""
    if not title:
        raise AcademicGraphSyncError("external_paper_missing_identity", "外部引用论文缺少可用身份")
    return f"title:{title}|year:{year}"


def _graph_paper_record(
    *, kb_id: str, paper: dict[str, Any], academic_paper_id: int | None, is_library_paper: bool
) -> dict[str, Any]:
    identity = _identity_key(paper)
    external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    open_access = paper.get("openAccessPdf") if isinstance(paper.get("openAccessPdf"), dict) else {}
    return {
        "graph_paper_id": hashstr(f"{kb_id}:{identity}", length=32),
        "kb_id": kb_id,
        "academic_paper_id": academic_paper_id,
        "identity_key": identity,
        "semantic_scholar_id": paper.get("paperId"),
        "external_ids": external_ids,
        "title": str(paper.get("title") or "").strip(),
        "abstract": paper.get("abstract"),
        "publication_year": paper.get("year"),
        "venue": paper.get("venue"),
        "citation_count": paper.get("citationCount"),
        "reference_count": paper.get("referenceCount"),
        "influential_citation_count": paper.get("influentialCitationCount"),
        "is_open_access": bool(paper.get("isOpenAccess")),
        "open_access_url": open_access.get("url"),
        "is_library_paper": is_library_paper,
        "source": "semantic_scholar",
    }


def _author_records(kb_id: str, paper: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for author in paper.get("authors") or []:
        if not isinstance(author, dict) or not str(author.get("name") or "").strip():
            continue
        name = str(author["name"]).strip()
        normalized_name = " ".join(name.casefold().split())
        semantic_scholar_id = str(author.get("authorId") or "").strip() or None
        identity = f"s2:{semantic_scholar_id}" if semantic_scholar_id else f"name:{normalized_name}"
        records.append(
            {
                "author_id": hashstr(f"{kb_id}:{identity}", length=32),
                "kb_id": kb_id,
                "identity_key": identity,
                "semantic_scholar_id": semantic_scholar_id,
                "name": name,
                "normalized_name": normalized_name,
                "external_ids": {"SemanticScholar": semantic_scholar_id} if semantic_scholar_id else {},
            }
        )
    return records


def _topic_records(kb_id: str, paper: dict[str, Any]) -> list[dict[str, Any]]:
    topics: dict[str, dict[str, Any]] = {}
    for name in paper.get("fieldsOfStudy") or []:
        normalized = " ".join(str(name).casefold().split())
        if normalized:
            topics[normalized] = {"name": str(name), "category": "field_of_study"}
    for item in paper.get("s2FieldsOfStudy") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("category") or "").strip()
        normalized = " ".join(name.casefold().split())
        if normalized:
            topics[normalized] = {"name": name, "category": item.get("source") or "s2_field_of_study"}
    return [
        {
            "topic_id": hashstr(f"{kb_id}:{normalized}", length=32),
            "kb_id": kb_id,
            "name": item["name"],
            "normalized_name": normalized,
            "category": item["category"],
            "source": "semantic_scholar",
        }
        for normalized, item in topics.items()
    ]


def _citation_record(
    *, kb_id: str, citing_paper_id: str, cited_paper_id: str, edge: dict[str, Any]
) -> dict[str, Any]:
    return {
        "citation_id": hashstr(f"{kb_id}:{citing_paper_id}:CITES:{cited_paper_id}", length=32),
        "kb_id": kb_id,
        "citing_paper_id": citing_paper_id,
        "cited_paper_id": cited_paper_id,
        "contexts": edge.get("contexts") if isinstance(edge.get("contexts"), list) else [],
        "intents": edge.get("intents") if isinstance(edge.get("intents"), list) else [],
        "is_influential": bool(edge.get("isInfluential")),
        "source": "semantic_scholar",
    }


async def _persist_paper(
    *,
    repo: AcademicGraphRepository,
    neo4j: AcademicGraphService,
    kb_id: str,
    paper: dict[str, Any],
    academic_paper_id: int | None,
    is_library_paper: bool,
) -> tuple[str, int, int]:
    graph_paper = _graph_paper_record(
        kb_id=kb_id,
        paper=paper,
        academic_paper_id=academic_paper_id,
        is_library_paper=is_library_paper,
    )
    if academic_paper_id is not None:
        valid, message = await repo.validate_local_identity(
            kb_id=kb_id,
            academic_paper_id=academic_paper_id,
            identity_key=graph_paper["identity_key"],
        )
        if not valid:
            raise AcademicGraphSyncError("graph_identity_conflict", message or "论文图谱身份冲突")
    graph_paper_id = await repo.upsert_graph_paper(graph_paper)
    authors = _author_records(kb_id, paper)
    topics = _topic_records(kb_id, paper)
    await repo.upsert_authors(graph_paper_id=graph_paper_id, authors=authors)
    await repo.upsert_topics(graph_paper_id=graph_paper_id, topics=topics)
    await neo4j.project_paper(
        kb_id=kb_id,
        paper={**graph_paper, "graph_paper_id": graph_paper_id},
        authors=authors,
        topics=topics,
    )
    return graph_paper_id, len(authors), len(topics)


async def _record_conflict(
    *, repo: AcademicGraphRepository, run_id: str, kb_id: str, paper: Any, exc: SemanticScholarError
) -> None:
    await repo.add_conflict(
        {
            "conflict_id": _conflict_id(run_id, paper.id, "identity", exc.error_type),
            "run_id": run_id,
            "kb_id": kb_id,
            "academic_paper_id": paper.id,
            "conflict_type": exc.error_type,
            "field_name": "identity",
            "local_value": {"title": paper.title, "year": paper.publication_year, "doi": paper.doi},
            "remote_value": None,
            "message": exc.message,
            "resolution_status": "unresolved",
        }
    )


def _conflict_id(run_id: str, academic_paper_id: int, field_name: str, conflict_type: str) -> str:
    return hashstr(f"{run_id}:{academic_paper_id}:{field_name}:{conflict_type}", length=32)


async def _persist_sync_checkpoint(
    repo: AcademicGraphRepository,
    run_id: str,
    counts: dict[str, int],
    processed_paper_ids: set[str],
) -> None:
    await repo.update_sync_run(
        run_id,
        {**counts, "processed_paper_ids": sorted(processed_paper_ids)},
    )


async def _ensure_graph_sync_owner_can_write(run: Any) -> None:
    user = await UserRepository().get_by_uid(str(run.uid))
    if user is None or bool(user.is_deleted):
        raise AcademicGraphSyncError("forbidden", "图谱同步任务所有者不存在或已删除")
    try:
        await _ensure_access(user, str(run.kb_id), write=True)
    except HTTPException as exc:
        raise AcademicGraphSyncError("forbidden", "图谱同步任务所有者已失去知识库写入权限") from exc


async def _run_sync(
    context: TaskContext,
    *,
    run_id: str,
    kb_id: str,
    paper_ids: list[str],
    citation_limit: int,
    reference_limit: int,
) -> dict[str, Any]:
    repo = AcademicGraphRepository()
    paper_repo = AcademicPaperRepository()
    client = SemanticScholarClient()
    neo4j = AcademicGraphService()
    run = await repo.get_sync_run(run_id)
    if run is None:
        raise AcademicGraphSyncError("graph_sync_run_missing", "学术图谱同步运行不存在")
    processed_paper_ids = {
        str(item).strip() for item in (run.processed_paper_ids or []) if str(item).strip()
    }
    await repo.update_sync_run(
        run_id,
        {
            "status": "running",
            "started_at": utc_now_naive(),
            "completed_at": None,
            "error_type": None,
            "error_message": None,
        },
    )
    counts = {
        "processed_papers": len(processed_paper_ids),
        "graph_papers": int(run.graph_papers or 0),
        "citations": int(run.citations or 0),
        "authors": int(run.authors or 0),
        "topics": int(run.topics or 0),
        "conflict_count": int(run.conflict_count or 0),
    }
    try:
        await _ensure_graph_sync_owner_can_write(run)
        papers = await paper_repo.list_for_graph_sync(kb_id=kb_id, paper_ids=paper_ids or None)
        if paper_ids and len(papers) != len(set(paper_ids)):
            raise AcademicGraphSyncError("paper_not_found", "部分待同步论文不存在于当前知识库")
        if not papers:
            raise AcademicGraphSyncError("paper_not_found", "当前知识库没有可同步论文")
        for index, local_paper in enumerate(papers, start=1):
            if local_paper.paper_id in processed_paper_ids:
                continue
            await context.raise_if_cancelled()
            await _ensure_graph_sync_owner_can_write(run)
            await context.set_progress((index - 1) / len(papers) * 90, f"正在同步 {local_paper.title}")
            try:
                remote = await client.resolve_paper(local_paper)
            except SemanticScholarError as exc:
                if exc.error_type.startswith("paper_"):
                    await _record_conflict(repo=repo, run_id=run_id, kb_id=kb_id, paper=local_paper, exc=exc)
                    counts["conflict_count"] += 1
                    processed_paper_ids.add(local_paper.paper_id)
                    counts["processed_papers"] = len(processed_paper_ids)
                    await _persist_sync_checkpoint(repo, run_id, counts, processed_paper_ids)
                    continue
                raise

            await _ensure_graph_sync_owner_can_write(run)
            try:
                local_graph_id, author_count, topic_count = await _persist_paper(
                    repo=repo,
                    neo4j=neo4j,
                    kb_id=kb_id,
                    paper=remote,
                    academic_paper_id=local_paper.id,
                    is_library_paper=True,
                )
            except AcademicGraphSyncError as exc:
                if exc.error_type != "graph_identity_conflict":
                    raise
                await repo.add_conflict(
                    {
                        "conflict_id": _conflict_id(run_id, local_paper.id, "identity_key", exc.error_type),
                        "run_id": run_id,
                        "kb_id": kb_id,
                        "academic_paper_id": local_paper.id,
                        "conflict_type": exc.error_type,
                        "field_name": "identity_key",
                        "local_value": local_paper.external_ids or {},
                        "remote_value": remote.get("externalIds") or {},
                        "message": exc.message,
                        "resolution_status": "unresolved",
                    }
                )
                counts["conflict_count"] += 1
                processed_paper_ids.add(local_paper.paper_id)
                counts["processed_papers"] = len(processed_paper_ids)
                await _persist_sync_checkpoint(repo, run_id, counts, processed_paper_ids)
                continue
            counts["graph_papers"] += 1
            counts["authors"] += author_count
            counts["topics"] += topic_count
            await paper_repo.update_external_metadata(
                kb_id=kb_id,
                paper_id=local_paper.paper_id,
                external_ids={
                    **(local_paper.external_ids or {}),
                    **(
                        remote.get("externalIds")
                        if isinstance(remote.get("externalIds"), dict)
                        else {}
                    ),
                    "SemanticScholar": remote["paperId"],
                },
                citation_count=remote.get("citationCount"),
            )

            citations, references = await asyncio.gather(
                client.list_citations(remote["paperId"], citation_limit),
                client.list_references(remote["paperId"], reference_limit),
            )
            await _ensure_graph_sync_owner_can_write(run)
            for edge_index, (edge, paper_field, local_is_citing) in enumerate([
                *((edge, "citingPaper", False) for edge in citations),
                *((edge, "citedPaper", True) for edge in references),
            ]):
                if edge_index and edge_index % 100 == 0:
                    await _ensure_graph_sync_owner_can_write(run)
                related = edge.get(paper_field)
                if not isinstance(related, dict) or not related.get("paperId") or not related.get("title"):
                    logger.warning("跳过无效引用边: paperId 或 title 缺失 (paper=%s)", local_paper.paper_id)
                    continue
                related_graph_id, related_author_count, related_topic_count = await _persist_paper(
                    repo=repo,
                    neo4j=neo4j,
                    kb_id=kb_id,
                    paper=related,
                    academic_paper_id=None,
                    is_library_paper=False,
                )
                counts["graph_papers"] += 1
                counts["authors"] += related_author_count
                counts["topics"] += related_topic_count
                citation = _citation_record(
                    kb_id=kb_id,
                    citing_paper_id=local_graph_id if local_is_citing else related_graph_id,
                    cited_paper_id=related_graph_id if local_is_citing else local_graph_id,
                    edge=edge,
                )
                await repo.upsert_citation(citation)
                await neo4j.project_citation(kb_id=kb_id, citation=citation)
                counts["citations"] += 1
            processed_paper_ids.add(local_paper.paper_id)
            counts["processed_papers"] = len(processed_paper_ids)
            await _persist_sync_checkpoint(repo, run_id, counts, processed_paper_ids)

        await context.raise_if_cancelled()
        await _ensure_graph_sync_owner_can_write(run)
        graph_counts = await repo.counts(kb_id)
        counts.update(
            {
                "graph_papers": graph_counts["papers"],
                "citations": graph_counts["citations"],
                "authors": graph_counts["authors"],
                "topics": graph_counts["topics"],
                "status": "completed_with_conflicts" if counts["conflict_count"] else "success",
                "completed_at": utc_now_naive(),
            }
        )
        await _persist_sync_checkpoint(repo, run_id, counts, processed_paper_ids)
        await context.set_progress(100, "学术引用图谱同步完成")
        result = {k: v for k, v in counts.items() if k not in ("completed_at",)}
        await context.set_result({"run_id": run_id, **result})
        return {"run_id": run_id, **counts}
    except asyncio.CancelledError:
        if context.cancellation_reason == "shutdown":
            await repo.update_sync_run(
                run_id,
                {
                    **counts,
                    "status": "pending",
                    "error_type": "graph_sync_recovery_pending",
                    "error_message": "服务重启，学术图谱同步等待自动恢复",
                    "completed_at": None,
                },
            )
        else:
            timed_out = context.cancellation_reason == "timeout"
            await repo.update_sync_run(
                run_id,
                {
                    **counts,
                    "status": "failed" if timed_out else "cancelled",
                    "error_type": "graph_sync_timeout" if timed_out else "graph_sync_cancelled",
                    "error_message": "学术图谱同步任务执行超时" if timed_out else "学术图谱同步任务已取消",
                    "completed_at": utc_now_naive(),
                },
            )
        raise
    except Exception as exc:
        if isinstance(exc, AcademicGraphSyncError):
            failure = exc
        elif isinstance(exc, SemanticScholarError):
            failure = AcademicGraphSyncError(exc.error_type, exc.message)
        else:
            failure = AcademicGraphSyncError("academic_graph_sync_failed", "学术图谱同步任务执行失败")
        await repo.update_sync_run(
            run_id,
            {
                **counts,
                "status": "failed",
                "error_type": failure.error_type,
                "error_message": failure.message,
                "completed_at": utc_now_naive(),
            },
        )
        if failure is exc:
            raise
        raise failure from exc


async def enqueue_academic_graph_sync(
    *,
    kb_id: str,
    current_user: User,
    paper_ids: list[str],
    citation_limit: int,
    reference_limit: int,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id, write=True)
    normalized_ids = list(dict.fromkeys(item.strip() for item in paper_ids if item.strip()))
    run_id = uuid.uuid4().hex
    sync_config = {"citation_limit": citation_limit, "reference_limit": reference_limit, "provider": "semantic_scholar"}
    repo = AcademicGraphRepository()
    await repo.create_sync_run(
        run_id=run_id,
        kb_id=kb_id,
        uid=str(current_user.uid),
        requested_paper_ids=normalized_ids,
        sync_config=sync_config,
    )

    try:
        task, created = await tasker.enqueue_unique_by_payload(
            name="同步学术引用图谱",
            task_type="academic_graph_sync",
            payload={"run_id": run_id, "kb_id": kb_id, **sync_config},
            payload_match={"kb_id": kb_id},
            statuses={"pending", "running"},
            coroutine=_resume_academic_graph_sync_task,
        )
        if not created:
            await repo.update_sync_run(
                run_id,
                {
                    "status": "failed",
                    "error_type": "graph_sync_active",
                    "error_message": "当前知识库已有学术图谱同步任务运行中",
                    "completed_at": utc_now_naive(),
                },
            )
            raise AcademicGraphSyncError("graph_sync_active", "当前知识库已有学术图谱同步任务运行中")
    except Exception as exc:
        if isinstance(exc, AcademicGraphSyncError):
            raise
        await repo.update_sync_run(
            run_id,
            {
                "status": "failed",
                "error_type": "task_enqueue_failed",
                "error_message": "学术图谱同步任务提交失败",
                "completed_at": utc_now_naive(),
            },
        )
        raise AcademicGraphSyncError("task_enqueue_failed", "学术图谱同步任务提交失败") from exc
    return {"run_id": run_id, "task_id": task.id, "status": task.status}


async def _resume_academic_graph_sync_task(context: TaskContext) -> dict[str, Any]:
    run_id = str(context.payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("学术图谱同步恢复缺少运行标识")

    repo = AcademicGraphRepository()
    run = await repo.get_sync_run(run_id)
    if run is None:
        raise RuntimeError("学术图谱同步运行不存在，无法恢复")
    if run.status in {"success", "completed_with_conflicts"}:
        return {
            "run_id": run_id,
            "status": run.status,
            "processed_papers": run.processed_papers,
            "graph_papers": run.graph_papers,
            "citations": run.citations,
            "authors": run.authors,
            "topics": run.topics,
            "conflict_count": run.conflict_count,
        }
    if run.status in {"failed", "cancelled"}:
        raise RuntimeError(f"学术图谱同步已处于 {run.status} 状态，不能恢复")

    user = await UserRepository().get_by_uid(str(run.uid))
    if user is None or bool(user.is_deleted):
        error = "图谱同步任务所有者不存在或已删除"
        await repo.update_sync_run(
            run_id,
            {
                "status": "failed",
                "error_type": "graph_sync_recovery_invalid",
                "error_message": error,
                "completed_at": utc_now_naive(),
            },
        )
        raise RuntimeError(error)
    try:
        await _ensure_access(user, str(run.kb_id), write=True)
    except HTTPException as exc:
        error = "图谱同步任务所有者已失去知识库写入权限"
        await repo.update_sync_run(
            run_id,
            {
                "status": "failed",
                "error_type": "graph_sync_recovery_invalid",
                "error_message": error,
                "completed_at": utc_now_naive(),
            },
        )
        raise RuntimeError(error) from exc

    config = run.sync_config or {}
    return await _run_sync(
        context,
        run_id=run_id,
        kb_id=str(run.kb_id),
        paper_ids=list(run.requested_paper_ids or []),
        citation_limit=int(config.get("citation_limit") or 0),
        reference_limit=int(config.get("reference_limit") or 0),
    )


async def recover_academic_graph_sync_runs() -> int:
    recovered = 0
    repo = AcademicGraphRepository()
    for run in await repo.list_recoverable_sync_runs():
        config = run.sync_config or {}
        user = await UserRepository().get_by_uid(str(run.uid))
        if user is None or bool(user.is_deleted):
            await repo.update_sync_run(
                str(run.run_id),
                {
                    "status": "failed",
                    "error_type": "graph_sync_recovery_invalid",
                    "error_message": "图谱同步任务所有者不存在或已删除",
                    "completed_at": utc_now_naive(),
                },
            )
            continue
        try:
            await _ensure_access(user, str(run.kb_id), write=True)
        except HTTPException:
            await repo.update_sync_run(
                str(run.run_id),
                {
                    "status": "failed",
                    "error_type": "graph_sync_recovery_invalid",
                    "error_message": "图谱同步任务所有者已失去知识库写入权限",
                    "completed_at": utc_now_naive(),
                },
            )
            continue

        try:
            _, created = await tasker.enqueue_unique_by_payload(
                name="恢复学术引用图谱同步",
                task_type="academic_graph_sync",
                payload={"run_id": run.run_id, "kb_id": run.kb_id, **config},
                payload_match={"run_id": run.run_id},
                statuses={"pending", "running"},
                coroutine=_resume_academic_graph_sync_task,
            )
        except Exception:
            await repo.update_sync_run(
                str(run.run_id),
                {
                    "status": "failed",
                    "error_type": "graph_sync_recovery_failed",
                    "error_message": "学术图谱同步恢复任务提交失败",
                    "completed_at": utc_now_naive(),
                },
            )
            continue
        recovered += int(created)
    return recovered


tasker.register_resumable_handler("academic_graph_sync", _resume_academic_graph_sync_task)


async def get_academic_graph_sync_run(*, run_id: str, current_user: User) -> dict[str, Any]:
    record = await AcademicGraphRepository().get_sync_run(run_id)
    if record is None:
        raise AcademicGraphSyncError("sync_run_not_found", "学术图谱同步运行不存在")
    await _ensure_access(current_user, str(record.kb_id))
    if str(record.uid) != str(current_user.uid) and current_user.role not in {"admin", "superadmin"}:
        raise AcademicGraphSyncError("forbidden", "无权查看该同步运行")
    return {
        "run_id": record.run_id,
        "kb_id": record.kb_id,
        "status": record.status,
        "processed_papers": record.processed_papers,
        "graph_papers": record.graph_papers,
        "citations": record.citations,
        "authors": record.authors,
        "topics": record.topics,
        "conflict_count": record.conflict_count,
        "error_type": record.error_type,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }


async def list_academic_graph_conflicts(
    *,
    run_id: str,
    current_user: User,
    resolution_status: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    repo = AcademicGraphRepository()
    run = await repo.get_sync_run(run_id)
    if run is None:
        raise AcademicGraphSyncError("sync_run_not_found", "学术图谱同步运行不存在")
    await _ensure_access(current_user, str(run.kb_id))
    if str(run.uid) != str(current_user.uid) and current_user.role not in {"admin", "superadmin"}:
        raise AcademicGraphSyncError("forbidden", "无权查看该同步运行的冲突")
    conflicts, total = await repo.list_conflicts(
        run_id=run_id,
        resolution_status=resolution_status,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [
            {
                "conflict_id": item.conflict_id,
                "academic_paper_id": item.academic_paper_id,
                "conflict_type": item.conflict_type,
                "field_name": item.field_name,
                "local_value": item.local_value,
                "remote_value": item.remote_value,
                "message": item.message,
                "resolution_status": item.resolution_status,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in conflicts
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


async def get_academic_graph_network(
    *, kb_id: str, current_user: User, center_paper_id: str | None, depth: int, limit: int
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    network = await AcademicGraphService().get_network(
        kb_id=kb_id,
        center_paper_id=center_paper_id,
        depth=depth,
        limit=limit,
    )
    counts = await AcademicGraphRepository().counts(kb_id)
    return {**network, "counts": counts}


async def get_academic_graph_relations(
    *, kb_id: str, current_user: User, graph_paper_id: str, limit: int
) -> dict[str, Any]:
    """返回某篇图谱论文的可解释两跳引用关联，包含中间节点、边方向与引用上下文。"""
    await _ensure_access(current_user, kb_id)
    result = await AcademicGraphRepository().find_two_hop_relations(
        kb_id=kb_id,
        source_graph_paper_id=graph_paper_id,
        limit=limit,
    )
    if result is None:
        raise AcademicGraphSyncError("graph_paper_not_found", "引用图谱论文节点不存在")

    papers = result["papers"]
    paths_by_target: dict[str, dict[str, Any]] = {}
    for path in result["paths"]:
        target_id = path["target_graph_paper_id"]
        if target_id in paths_by_target:
            continue
        paths_by_target[target_id] = {
            "target_graph_paper_id": target_id,
            "intermediate_graph_paper_id": path["intermediate_graph_paper_id"],
            "relation_type": path["relation_type"],
            "edges": [
                {
                    "citation_id": edge.citation_id,
                    "source_graph_paper_id": edge.citing_paper_id,
                    "target_graph_paper_id": edge.cited_paper_id,
                    "direction": "outgoing"
                    if edge.citing_paper_id == (graph_paper_id if index == 0 else path["intermediate_graph_paper_id"])
                    else "incoming",
                    "is_influential": bool(edge.is_influential),
                    "contexts": edge.contexts or [],
                    "intents": edge.intents or [],
                }
                for index, edge in enumerate(path["edges"])
            ],
        }

    def serialize_paper(paper_id: str) -> dict[str, Any] | None:
        paper = papers.get(paper_id)
        if paper is None:
            return None
        return {
            "graph_paper_id": paper.graph_paper_id,
            "academic_paper_id": paper.academic_paper_id,
            "title": paper.title,
            "abstract": paper.abstract,
            "publication_year": paper.publication_year,
            "venue": paper.venue,
            "citation_count": paper.citation_count,
            "is_library_paper": bool(paper.is_library_paper),
            "semantic_scholar_id": paper.semantic_scholar_id,
        }

    relations = []
    for path in paths_by_target.values():
        relations.append(
            {
                **path,
                "source": serialize_paper(graph_paper_id),
                "intermediate": serialize_paper(path["intermediate_graph_paper_id"]),
                "target": serialize_paper(path["target_graph_paper_id"]),
            }
        )
    relations.sort(key=lambda item: (item["relation_type"], item["target_graph_paper_id"]))
    return {
        "source": serialize_paper(graph_paper_id),
        "relations": relations[: min(max(int(limit), 1), 100)],
        "candidate_path_count": len(result["paths"]),
    }


__all__ = [
    "AcademicGraphSyncError",
    "enqueue_academic_graph_sync",
    "get_academic_graph_network",
    "get_academic_graph_relations",
    "get_academic_graph_sync_run",
    "list_academic_graph_conflicts",
    "recover_academic_graph_sync_runs",
]
