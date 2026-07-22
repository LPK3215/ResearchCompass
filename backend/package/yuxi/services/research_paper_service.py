from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User


def _serialize_paper(paper) -> dict[str, Any]:
    return {
        "paper_id": paper.paper_id,
        "kb_id": paper.kb_id,
        "file_id": paper.file_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": paper.authors or [],
        "publication_year": paper.publication_year,
        "venue": paper.venue,
        "doi": paper.doi,
        "keywords": paper.keywords or [],
        "language": paper.language,
        "external_ids": paper.external_ids or {},
        "citation_count": paper.citation_count,
        "metadata_source": paper.metadata_source or "document",
        "metadata_status": paper.metadata_status or "extracted",
        "metadata_error": paper.metadata_error,
        "metadata_revision": int(paper.metadata_revision or 1),
        "indexed_revision": int(paper.indexed_revision or 0),
        "created_at": paper.created_at.isoformat() if paper.created_at else None,
        "updated_at": paper.updated_at.isoformat() if paper.updated_at else None,
    }


async def _ensure_access(current_user: User, kb_id: str, *, write: bool = False) -> None:
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")

    user_info = {
        "uid": str(current_user.uid),
        "role": current_user.role,
        "department_id": current_user.department_id,
    }
    if not await knowledge_base.check_accessible(user_info, kb_id):
        raise HTTPException(status_code=403, detail="无权访问该知识库")
    if write and current_user.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="需要管理员权限")


async def list_papers_view(
    *,
    kb_id: str,
    current_user: User,
    query: str | None,
    year_from: int | None,
    year_to: int | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    offset = (page - 1) * page_size
    papers, total = await AcademicPaperRepository().list_by_kb_id(
        kb_id=kb_id,
        query=query,
        year_from=year_from,
        year_to=year_to,
        offset=offset,
        limit=page_size,
    )
    return {
        "items": [_serialize_paper(paper) for paper in papers],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": offset + len(papers) < total,
    }


async def get_paper_view(*, kb_id: str, paper_id: str, current_user: User) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")

    file_record = await KnowledgeFileRepository().get_by_file_id(paper.file_id)
    chunks = await KnowledgeChunkRepository().list_by_file_id(paper.file_id)
    academic_chunks = [
        chunk
        for chunk in chunks
        if isinstance(chunk.chunk_metadata, dict)
        and chunk.chunk_metadata.get("document_type") == "academic_paper"
    ]
    section_counts = Counter(
        str(chunk.chunk_metadata.get("section_type") or "other") for chunk in academic_chunks
    )
    sections = []
    seen: set[tuple[str, str | None]] = set()
    for chunk in academic_chunks:
        metadata = chunk.chunk_metadata
        identity = (str(metadata.get("section_type") or "other"), metadata.get("section_title"))
        if identity in seen:
            continue
        seen.add(identity)
        sections.append(
            {
                "section_type": identity[0],
                "section_title": identity[1],
                "section_path": metadata.get("section_path") or [],
                "first_chunk_index": chunk.chunk_index,
            }
        )

    return {
        "paper": _serialize_paper(paper),
        "file": {
            "file_id": paper.file_id,
            "filename": file_record.filename if file_record else None,
            "status": file_record.status if file_record else None,
            "file_type": file_record.file_type if file_record else None,
            "chunk_count": len(academic_chunks),
        },
        "sections": sections,
        "section_counts": dict(section_counts),
    }


async def list_paper_chunks_view(
    *,
    kb_id: str,
    paper_id: str,
    current_user: User,
    section_type: str | None,
    chunk_id: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    chunks, total = await KnowledgeChunkRepository().list_academic_by_file_id(
        file_id=paper.file_id,
        section_type=section_type,
        chunk_id=chunk_id,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [
            {
                "chunk_id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "start_char_pos": chunk.start_char_pos,
                "end_char_pos": chunk.end_char_pos,
                "metadata": chunk.chunk_metadata or {},
            }
            for chunk in chunks
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(chunks) < total,
    }


async def get_paper_evidence_view(
    *, kb_id: str, paper_id: str, chunk_id: str, current_user: User
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    chunk = await KnowledgeChunkRepository().get_by_chunk_id(chunk_id)
    if chunk is None or chunk.file_id != paper.file_id:
        raise HTTPException(status_code=404, detail="论文证据分块不存在")
    metadata = chunk.chunk_metadata if isinstance(chunk.chunk_metadata, dict) else {}
    file_record = await KnowledgeFileRepository().get_by_file_id(paper.file_id)
    return {
        "paper": _serialize_paper(paper),
        "file": {
            "file_id": paper.file_id,
            "filename": file_record.filename if file_record else None,
            "file_type": file_record.file_type if file_record else None,
            "original_available": bool(file_record and (file_record.minio_url or file_record.path)),
            "parsed_available": bool(file_record and file_record.markdown_file),
            "original_download_path": (
                f"/api/workspace/knowledge/download?kb_id={kb_id}&file_id={paper.file_id}&variant=original"
                if file_record and (file_record.minio_url or file_record.path)
                else None
            ),
            "parsed_download_path": (
                f"/api/workspace/knowledge/download?kb_id={kb_id}&file_id={paper.file_id}&variant=parsed"
                if file_record and file_record.markdown_file
                else None
            ),
        },
        "evidence": {
            "chunk_id": chunk.chunk_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "section_type": metadata.get("section_type"),
            "section_title": metadata.get("section_title"),
            "section_path": metadata.get("section_path") or [],
            "start_char_pos": chunk.start_char_pos,
            "end_char_pos": chunk.end_char_pos,
            "source_page_start": metadata.get("source_page_start"),
            "source_page_end": metadata.get("source_page_end"),
            "source_rects": metadata.get("source_rects") or [],
            "locator_type": metadata.get("locator_type") or "parsed_source_character_range",
        },
    }


async def update_paper_view(
    *, kb_id: str, paper_id: str, current_user: User, updates: dict[str, Any]
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id, write=True)
    paper_repo = AcademicPaperRepository()
    paper = await paper_repo.update_metadata(
        kb_id=kb_id,
        paper_id=paper_id,
        data={
            **updates,
            "metadata_source": "manual",
            "metadata_status": "pending_reindex",
            "metadata_error": None,
        },
    )
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")

    file_id = str(paper.file_id)
    paper_title = str(paper.title)
    operator_id = str(current_user.uid)
    revision = int(paper.metadata_revision or 1)
    try:
        task, created = await _enqueue_paper_reindex(
            kb_id=kb_id,
            paper_id=paper_id,
            file_id=file_id,
            paper_title=paper_title,
            operator_id=operator_id,
            revision=revision,
        )
    except Exception as exc:
        await paper_repo.fail_reindex(
            kb_id=kb_id,
            paper_id=paper_id,
            revision=revision,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"论文元数据同步任务提交失败: {exc}") from exc

    return {
        "paper": _serialize_paper(paper),
        "reindex_task": {"task_id": task.id, "status": task.status, "created": created},
    }


@asynccontextmanager
async def _paper_reindex_lock(kb_id: str, paper_id: str):
    lock_key = f"research-paper-reindex:{kb_id}:{paper_id}"
    async with pg_manager.get_async_session_context() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        yield


async def _run_paper_reindex(
    context: TaskContext,
    *,
    kb_id: str,
    paper_id: str,
    file_id: str,
    operator_id: str | None,
    target_revision: int,
) -> dict[str, Any]:
    paper_repo = AcademicPaperRepository()
    async with _paper_reindex_lock(kb_id, paper_id):
        while True:
            paper = await paper_repo.get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
            if paper is None:
                raise ValueError("论文不存在，无法同步检索索引")
            current_revision = int(paper.metadata_revision or 1)
            indexed_revision = int(paper.indexed_revision or 0)
            if indexed_revision >= target_revision and paper.metadata_status == "verified":
                return {
                    "paper_id": paper_id,
                    "file_id": file_id,
                    "revision": current_revision,
                    "skipped": True,
                }

            await context.set_progress(10.0, f"正在同步论文元数据版本 {current_revision}")
            try:
                result = await knowledge_base.index_file(
                    kb_id,
                    file_id,
                    operator_id=operator_id,
                    params={
                        "chunk_preset_id": "academic",
                        "chunk_parser_config": {"paper_metadata": _serialize_paper(paper)},
                    },
                )
                completed = await paper_repo.complete_reindex(
                    kb_id=kb_id,
                    paper_id=paper_id,
                    revision=current_revision,
                )
                if completed:
                    payload = {
                        "paper_id": paper_id,
                        "file_id": file_id,
                        "revision": current_revision,
                        "index_result": result,
                    }
                    await context.set_result(payload)
                    await context.set_progress(100.0, "论文元数据与检索索引同步完成")
                    return payload
                await context.set_progress(65.0, "检测到更新版本，继续同步最新论文元数据")
            except Exception as exc:
                await paper_repo.fail_reindex(
                    kb_id=kb_id,
                    paper_id=paper_id,
                    revision=current_revision,
                    error=str(exc),
                )
                raise


async def _enqueue_paper_reindex(
    *,
    kb_id: str,
    paper_id: str,
    file_id: str,
    paper_title: str,
    operator_id: str | None,
    revision: int,
):
    async def reindex(context: TaskContext) -> dict[str, Any]:
        return await _run_paper_reindex(
            context,
            kb_id=kb_id,
            paper_id=paper_id,
            file_id=file_id,
            operator_id=operator_id,
            target_revision=revision,
        )

    payload = {"kb_id": kb_id, "paper_id": paper_id, "file_id": file_id, "revision": revision}
    return await tasker.enqueue_unique_by_payload(
        name=f"论文元数据同步 ({paper_title})",
        task_type="research_paper_reindex",
        payload=payload,
        payload_match={"kb_id": kb_id, "paper_id": paper_id, "revision": revision},
        statuses={"pending", "running"},
        coroutine=reindex,
    )


async def recover_pending_paper_reindexes() -> int:
    recovered = 0
    for paper in await AcademicPaperRepository().list_pending_reindex():
        _, created = await _enqueue_paper_reindex(
            kb_id=str(paper.kb_id),
            paper_id=str(paper.paper_id),
            file_id=str(paper.file_id),
            paper_title=str(paper.title),
            operator_id=None,
            revision=int(paper.metadata_revision or 1),
        )
        recovered += int(created)
    return recovered
