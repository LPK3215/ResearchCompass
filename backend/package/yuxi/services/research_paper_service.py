"""ResearchCompass 论文资料库与元数据同步服务。

本模块是本仓库作者在开源智能体框架 Yuxi 之上实现的论文治理业务：维护论文元数据的
查看、编辑、标签、BibTeX 导出，以及"元数据修订 → 重新切分索引"的异步同步流程。
文件检索、切片索引、任务调度等通用知识库与任务运行时由 Yuxi 提供；本模块定义论文
元数据的版本语义、再索引任务的可恢复执行协议，以及面向科研用户的论文视图投影。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import re
from typing import Any
import unicodedata

from fastapi import HTTPException
from sqlalchemy import text

from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.task_service import PublicTaskError, TaskContext, tasker
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger


class ResearchPaperReindexError(PublicTaskError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


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
    sort_by: str = "year",
    sort_order: str = "desc",
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
        sort_by=sort_by,
        sort_order=sort_order,
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
    academic_chunks = await KnowledgeChunkRepository().list_academic_chunk_metadata_by_file_id(paper.file_id)
    section_counts = Counter(
        str(metadata.get("section_type") or "other") for _, metadata in academic_chunks if isinstance(metadata, dict)
    )
    sections = []
    seen: set[tuple[str, str | None]] = set()
    for chunk_index, metadata in academic_chunks:
        if not isinstance(metadata, dict):
            continue
        identity = (str(metadata.get("section_type") or "other"), metadata.get("section_title"))
        if identity in seen:
            continue
        seen.add(identity)
        sections.append(
            {
                "section_type": identity[0],
                "section_title": identity[1],
                "section_path": metadata.get("section_path") or [],
                "first_chunk_index": chunk_index,
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


async def get_paper_evidence_view(*, kb_id: str, paper_id: str, chunk_id: str, current_user: User) -> dict[str, Any]:
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
        error_message = "论文元数据同步任务提交失败"
        await paper_repo.fail_reindex(
            kb_id=kb_id,
            paper_id=paper_id,
            revision=revision,
            error=error_message,
        )
        raise HTTPException(status_code=500, detail=error_message) from exc

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
            await context.raise_if_cancelled()
            current_revision = target_revision
            try:
                await _ensure_reindex_owner_can_write(operator_id, kb_id)
                paper = await paper_repo.get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
                if paper is None:
                    raise ResearchPaperReindexError("paper_not_found", "论文不存在，无法同步检索索引")
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
                result = await knowledge_base.index_file(
                    kb_id,
                    file_id,
                    operator_id=operator_id,
                    params={
                        "chunk_preset_id": "academic",
                        "chunk_parser_config": {"paper_metadata": _serialize_paper(paper)},
                    },
                )
                await context.raise_if_cancelled()
                await _ensure_reindex_owner_can_write(operator_id, kb_id)
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
            except ResearchPaperReindexError as exc:
                await paper_repo.fail_reindex(
                    kb_id=kb_id,
                    paper_id=paper_id,
                    revision=current_revision,
                    error=exc.message,
                )
                raise
            except Exception as exc:
                failure = ResearchPaperReindexError("paper_reindex_failed", "论文元数据与检索索引同步失败")
                await paper_repo.fail_reindex(
                    kb_id=kb_id,
                    paper_id=paper_id,
                    revision=current_revision,
                    error=failure.message,
                )
                raise failure from exc


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
        return await _resume_paper_reindex_task(context)

    payload = {
        "kb_id": kb_id,
        "paper_id": paper_id,
        "file_id": file_id,
        "operator_id": operator_id,
        "revision": revision,
    }
    return await tasker.enqueue_unique_by_payload(
        name=f"论文元数据同步 ({paper_title})",
        task_type="research_paper_reindex",
        payload=payload,
        payload_match={"kb_id": kb_id, "paper_id": paper_id, "revision": revision},
        statuses={"pending", "running"},
        coroutine=reindex,
    )


async def _ensure_reindex_owner_can_write(operator_id: str | None, kb_id: str) -> User:
    normalized_operator_id = str(operator_id or "").strip()
    if not normalized_operator_id:
        raise ResearchPaperReindexError("forbidden", "论文元数据同步任务缺少所有者")
    user = await UserRepository().get_by_uid(normalized_operator_id)
    if user is None or bool(user.is_deleted):
        raise ResearchPaperReindexError("forbidden", "论文元数据同步任务所有者不存在或已删除")
    try:
        await _ensure_access(user, kb_id, write=True)
    except HTTPException as exc:
        raise ResearchPaperReindexError("forbidden", "论文元数据同步任务所有者已失去知识库写入权限") from exc
    return user


async def _resume_paper_reindex_task(context: TaskContext) -> dict[str, Any]:
    """恢复已持久化的论文再索引任务，并在执行前重新校验原写入者仍持有写权限。"""
    payload = context.payload
    try:
        kb_id = str(payload["kb_id"])
        paper_id = str(payload["paper_id"])
        file_id = str(payload["file_id"])
        revision = int(payload["revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("论文元数据同步恢复参数无效") from exc

    operator_id = str(payload.get("operator_id") or "").strip() or None
    try:
        await _ensure_reindex_owner_can_write(operator_id, kb_id)
    except ResearchPaperReindexError as exc:
        await AcademicPaperRepository().fail_reindex(
            kb_id=kb_id,
            paper_id=paper_id,
            revision=revision,
            error=str(exc),
        )
        raise

    return await _run_paper_reindex(
        context,
        kb_id=kb_id,
        paper_id=paper_id,
        file_id=file_id,
        operator_id=operator_id,
        target_revision=revision,
    )


async def recover_pending_paper_reindexes() -> int:
    recovered = 0
    paper_repo = AcademicPaperRepository()
    for paper in await paper_repo.list_pending_reindex():
        revision = int(paper.metadata_revision or 1)
        previous_task = await tasker.find_task_by_payload(
            task_type="research_paper_reindex",
            payload_match={
                "kb_id": str(paper.kb_id),
                "paper_id": str(paper.paper_id),
                "revision": revision,
            },
        )
        operator_id = str((previous_task.payload if previous_task else {}).get("operator_id") or "").strip()
        if not operator_id:
            await paper_repo.fail_reindex(
                kb_id=str(paper.kb_id),
                paper_id=str(paper.paper_id),
                revision=revision,
                error="论文元数据同步恢复任务缺少可验证的所有者",
            )
            continue
        try:
            _, created = await _enqueue_paper_reindex(
                kb_id=str(paper.kb_id),
                paper_id=str(paper.paper_id),
                file_id=str(paper.file_id),
                paper_title=str(paper.title),
                operator_id=operator_id,
                revision=revision,
            )
        except Exception as exc:
            logger.error(
                "恢复论文元数据同步任务入队失败: kb_id=%s paper_id=%s exception_type=%s",
                paper.kb_id,
                paper.paper_id,
                type(exc).__name__,
            )
            await paper_repo.fail_reindex(
                kb_id=str(paper.kb_id),
                paper_id=str(paper.paper_id),
                revision=revision,
                error="论文元数据同步恢复任务提交失败",
            )
            continue
        recovered += int(created)
    return recovered


tasker.register_resumable_handler("research_paper_reindex", _resume_paper_reindex_task)


def _escape_bibtex(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in str(value or ""))


def _bibtex_key(paper: Any, index: int) -> str:
    first_author = (paper.authors or ["anon"])[0] if paper.authors else "anon"
    surname = first_author.split()[-1] if first_author else "anon"
    normalized_surname = unicodedata.normalize("NFKD", surname).encode("ascii", "ignore").decode("ascii")
    safe_surname = re.sub(r"[^A-Za-z0-9_-]+", "", normalized_surname)
    return f"{safe_surname or 'anon'}{paper.publication_year or 'nd'}{index}"


def _format_bibtex_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    return " and ".join(authors)


def _paper_to_bibtex(paper: Any, index: int) -> str:
    key = _bibtex_key(paper, index)
    lines = [f"@article{{{key},"]
    lines.append(f"  title = {{{_escape_bibtex(paper.title)}}},")
    authors = _format_bibtex_authors(paper.authors or [])
    if authors:
        lines.append(f"  author = {{{_escape_bibtex(authors)}}},")
    if paper.publication_year is not None:
        lines.append(f"  year = {{{int(paper.publication_year)}}},")
    if paper.venue:
        lines.append(f"  journal = {{{_escape_bibtex(paper.venue)}}},")
    if paper.doi:
        lines.append(f"  doi = {{{_escape_bibtex(paper.doi)}}},")
    if paper.abstract:
        lines.append(f"  abstract = {{{_escape_bibtex(paper.abstract)}}},")
    keywords = paper.keywords or []
    if keywords:
        lines.append(f"  keywords = {{{_escape_bibtex(', '.join(keywords))}}},")
    lines.append("}")
    return "\n".join(lines)


async def export_papers_bibtex(
    *, kb_id: str, current_user: User, paper_ids: list[str] | None = None
) -> AsyncIterator[str]:
    await _ensure_access(current_user, kb_id)
    repo = AcademicPaperRepository()

    async def stream() -> AsyncIterator[str]:
        if paper_ids:
            papers = await repo.list_by_paper_ids(kb_id=kb_id, paper_ids=paper_ids)
            for index, paper in enumerate(papers, start=1):
                if index > 1:
                    yield "\n\n"
                yield _paper_to_bibtex(paper, index)
            return

        index = 0
        async for papers in repo.iter_export_batches(kb_id=kb_id):
            for paper in papers:
                index += 1
                if index > 1:
                    yield "\n\n"
                yield _paper_to_bibtex(paper, index)

    return stream()


async def list_user_tags_view(*, kb_id: str, current_user: User) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    tags = await AcademicPaperRepository().list_tags(kb_id=kb_id, uid=str(current_user.uid))
    return {"tags": tags}


async def list_paper_tags_view(*, kb_id: str, paper_id: str, current_user: User) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    tags = await AcademicPaperRepository().list_paper_tags(kb_id=kb_id, paper_id=paper_id, uid=str(current_user.uid))
    return {"paper_id": paper_id, "tags": tags}


async def add_paper_tag_view(*, kb_id: str, paper_id: str, tag: str, current_user: User) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    normalized = tag.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="标签不能为空")
    if len(normalized) > 64:
        raise HTTPException(status_code=422, detail="标签长度不能超过 64 个字符")
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    await AcademicPaperRepository().add_tag(kb_id=kb_id, paper_id=paper_id, uid=str(current_user.uid), tag=normalized)
    return {"paper_id": paper_id, "tag": normalized}


async def remove_paper_tag_view(*, kb_id: str, paper_id: str, tag: str, current_user: User) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    normalized = tag.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="标签不能为空")
    await AcademicPaperRepository().remove_tag(
        kb_id=kb_id, paper_id=paper_id, uid=str(current_user.uid), tag=normalized
    )
    return {"paper_id": paper_id, "tag": normalized, "removed": True}
