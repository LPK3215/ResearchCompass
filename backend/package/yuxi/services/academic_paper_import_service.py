from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from fastapi import HTTPException

from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.services.semantic_scholar_service import (
    SemanticScholarClient,
    SemanticScholarError,
    is_paper_identifier,
)
from yuxi.services.task_service import PublicTaskError, TaskContext, tasker
from yuxi.storage.minio.client import MinIOClient, get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.utils import hashstr, logger
from yuxi.knowledge.utils import calculate_content_hash


class AcademicPaperImportError(PublicTaskError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _safe_filename(title: str, paper_id: str) -> str:
    normalized = re.sub(r"[\\/:*?\"<>|]+", " ", title or "").strip()
    normalized = re.sub(r"\s+", " ", normalized)[:180].rstrip(" .")
    return f"{normalized or 'academic-paper'}-{paper_id[:12]}.pdf"


def _paper_metadata(paper: dict[str, Any]) -> dict[str, Any]:
    raw_external_ids = paper.get("externalIds")
    external_ids = raw_external_ids if isinstance(raw_external_ids, dict) else {}
    paper_id = str(paper.get("paperId") or "").strip()
    title = str(paper.get("title") or "").strip()
    if not paper_id or not title:
        raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 论文缺少 paperId 或标题")
    if len(paper_id) > 61:
        raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar paperId 超出系统长度限制")

    publication_year = paper.get("year")
    if publication_year is not None:
        if isinstance(publication_year, bool) or (
            isinstance(publication_year, float) and not publication_year.is_integer()
        ):
            raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 论文年份无效")
        try:
            publication_year = int(publication_year)
        except (TypeError, ValueError) as exc:
            raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 论文年份无效") from exc
        if not 1500 <= publication_year <= datetime.now(UTC).year + 1:
            raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 论文年份超出有效范围")

    citation_count = paper.get("citationCount")
    if citation_count is not None:
        if isinstance(citation_count, bool) or (
            isinstance(citation_count, float) and not citation_count.is_integer()
        ):
            raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 引用数无效")
        try:
            citation_count = int(citation_count)
        except (TypeError, ValueError) as exc:
            raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 引用数无效") from exc
        if citation_count < 0:
            raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 引用数不能为负数")

    venue = str(paper.get("venue") or "").strip() or None
    if venue and len(venue) > 512:
        raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 期刊或会议名称超出长度限制")
    doi = str(external_ids.get("DOI") or "").strip().lower() or None
    if doi and len(doi) > 512:
        raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar DOI 超出长度限制")
    normalized_external_ids: dict[str, str] = {}
    for key, value in external_ids.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 外部标识格式无效")
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            continue
        if len(normalized_key) > 64 or len(normalized_value) > 512:
            raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 外部标识超出长度限制")
        normalized_external_ids[normalized_key] = normalized_value
    if len(normalized_external_ids) > 32:
        raise AcademicPaperImportError("paper_metadata_invalid", "Semantic Scholar 外部标识数量超出限制")

    return {
        "paper_id": f"s2_{paper_id}",
        "title": title,
        "abstract": str(paper.get("abstract") or "").strip() or None,
        "authors": [
            str(author.get("name")).strip()
            for author in paper.get("authors") or []
            if isinstance(author, dict) and str(author.get("name") or "").strip()
        ],
        "publication_year": publication_year,
        "venue": venue,
        "doi": doi,
        "keywords": [str(value).strip() for value in paper.get("fieldsOfStudy") or [] if str(value).strip()],
        "language": "en",
        "metadata_source": "semantic_scholar",
        "metadata_status": "extracted",
        "external_ids": {
            **normalized_external_ids,
            "SemanticScholar": paper_id,
        },
        "citation_count": citation_count,
    }


def _open_access_url(paper: dict[str, Any]) -> str | None:
    open_access = paper.get("openAccessPdf")
    if not isinstance(open_access, dict):
        return None
    url = str(open_access.get("url") or "").strip()
    return url or None


async def search_external_papers(*, kb_id: str, current_user: User, query: str, limit: int) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    try:
        client = SemanticScholarClient()
        papers = [await client.get_paper(query)] if is_paper_identifier(query) else await client.search(query, limit)
    except SemanticScholarError as exc:
        raise AcademicPaperImportError(exc.error_type, exc.message) from exc
    return {
        "items": [
            {
                "paper_id": paper.get("paperId"),
                "title": paper.get("title"),
                "abstract": paper.get("abstract"),
                "authors": [
                    author.get("name")
                    for author in paper.get("authors") or []
                    if isinstance(author, dict) and author.get("name")
                ],
                "publication_year": paper.get("year"),
                "venue": paper.get("venue"),
                "doi": (paper.get("externalIds") or {}).get("DOI")
                if isinstance(paper.get("externalIds"), dict)
                else None,
                "citation_count": paper.get("citationCount"),
                "is_open_access": bool(paper.get("isOpenAccess")),
                "open_access_url": _open_access_url(paper),
                "external_ids": paper.get("externalIds") or {},
            }
            for paper in papers
        ]
    }


async def _run_import_task(
    context: TaskContext,
    *,
    kb_id: str,
    file_id: str,
    paper_metadata: dict[str, Any],
    operator_id: str,
) -> dict[str, Any]:
    try:
        await _ensure_import_owner_can_write(operator_id=operator_id, kb_id=kb_id, file_id=file_id)
        await context.raise_if_cancelled()
        file_info = await knowledge_base.get_file_basic_info(kb_id, file_id)
        file_meta = file_info.get("meta") if isinstance(file_info, dict) else None
        if not isinstance(file_meta, dict):
            raise ValueError("外部论文文件记录不存在")
        file_status = str(file_meta.get("status") or "uploaded")
        if file_status == "indexed":
            return {
                "kb_id": kb_id,
                "file_id": file_id,
                "paper_id": paper_metadata["paper_id"],
                "status": "indexed",
                "skipped": True,
            }

        parsed = file_meta
        if file_status in {"uploaded", "error_parsing", "failed"}:
            await context.set_progress(5, "准备解析外部论文")
            await _ensure_import_owner_can_write(operator_id=operator_id, kb_id=kb_id, file_id=file_id)
            parsed = await knowledge_base.parse_file(kb_id, file_id, operator_id=operator_id)
            await context.raise_if_cancelled()
        elif file_status == "indexing":
            await KnowledgeFileRepository().update_fields(
                file_id=file_id,
                kb_id=kb_id,
                data={"status": "error_indexing", "error_message": "服务重启后恢复外部论文导入"},
            )
            file_status = "error_indexing"

        if file_status not in {"parsed", "error_indexing", "indexed"} and str(parsed.get("status") or "") not in {
            "parsed",
            "error_indexing",
            "indexed",
        }:
            raise ValueError(f"外部论文文件处于不可索引状态: {file_status}")
        await context.set_progress(55, "正在建立学术分块和检索索引")
        await context.raise_if_cancelled()
        await _ensure_import_owner_can_write(operator_id=operator_id, kb_id=kb_id, file_id=file_id)
        indexed = await knowledge_base.index_file(
            kb_id,
            file_id,
            operator_id=operator_id,
            params={
                "chunk_preset_id": "academic",
                "chunk_parser_config": {"paper_metadata": paper_metadata},
            },
        )
        await context.raise_if_cancelled()
        await _ensure_import_owner_can_write(operator_id=operator_id, kb_id=kb_id, file_id=file_id)
        result = {
            "kb_id": kb_id,
            "file_id": file_id,
            "paper_id": paper_metadata["paper_id"],
            "status": indexed.get("status") if isinstance(indexed, dict) else "indexed",
            "parsed": parsed,
            "indexed": indexed,
        }
        await context.set_result(result)
        await context.set_progress(100, "外部论文已完成解析与索引")
        return result
    except AcademicPaperImportError:
        raise
    except Exception as exc:
        error_message = "外部论文解析与索引失败"
        logger.error(
            "外部论文导入任务失败: file_id=%s exception_type=%s",
            file_id,
            type(exc).__name__,
        )
        try:
            await KnowledgeFileRepository().update_fields(
                file_id=file_id,
                kb_id=kb_id,
                data={"status": "failed", "error_message": error_message},
            )
        except Exception as update_exc:
            logger.error(
                "更新外部论文导入失败状态失败: file_id=%s exception_type=%s",
                file_id,
                type(update_exc).__name__,
            )
        raise AcademicPaperImportError("paper_import_failed", error_message) from exc


async def _ensure_import_owner_can_write(*, operator_id: str, kb_id: str, file_id: str) -> User:
    file_repo = KnowledgeFileRepository()
    user = await UserRepository().get_by_uid(operator_id)
    if user is None or bool(user.is_deleted):
        error = "外部论文导入任务所有者不存在或已删除"
        await file_repo.update_fields(
            file_id=file_id,
            kb_id=kb_id,
            data={"status": "failed", "error_message": error},
        )
        raise AcademicPaperImportError("forbidden", error)
    try:
        await _ensure_access(user, kb_id, write=True)
    except HTTPException as exc:
        error = "外部论文导入任务所有者已失去知识库权限"
        await file_repo.update_fields(
            file_id=file_id,
            kb_id=kb_id,
            data={"status": "failed", "error_message": error},
        )
        raise AcademicPaperImportError("forbidden", error) from exc
    return user


async def _cleanup_failed_import(
    *,
    kb_id: str,
    minio_client: MinIOClient,
    object_name: str,
    file_id: str | None = None,
) -> None:
    if file_id:
        try:
            await knowledge_base.delete_file(kb_id, file_id)
        except Exception as exc:
            logger.error(
                "清理外部论文导入失败的文件记录失败: kb_id=%s file_id=%s exception_type=%s",
                kb_id,
                file_id,
                type(exc).__name__,
            )
    try:
        await minio_client.adelete_file(MinIOClient.KB_BUCKETS["documents"], object_name)
    except Exception as exc:
        logger.error(
            "清理外部论文导入失败的对象存储文件失败: kb_id=%s exception_type=%s",
            kb_id,
            type(exc).__name__,
        )


async def import_external_paper(*, identifier: str, kb_id: str, current_user: User) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id, write=True)
    support_info = await knowledge_base.get_database_document_support(kb_id)
    if not support_info[1]:
        raise AcademicPaperImportError("unsupported_knowledge_base", "当前知识库不支持论文文件导入")
    client = SemanticScholarClient()
    try:
        paper = await client.get_paper(identifier)
        paper_metadata = _paper_metadata(paper)
        existing = await AcademicPaperRepository().find_duplicate_for_import(
            kb_id=kb_id,
            paper_id=paper_metadata["paper_id"],
            doi=paper_metadata.get("doi"),
            external_ids=paper_metadata.get("external_ids"),
        )
        if existing is not None:
            raise AcademicPaperImportError("paper_already_imported", "该论文已经存在于当前知识库")
        pdf_url = _open_access_url(paper)
        if not pdf_url:
            raise AcademicPaperImportError("paper_pdf_unavailable", "该论文没有可下载的公开 PDF")
        pdf_bytes, final_url = await client.download_open_access_pdf(pdf_url)
    except SemanticScholarError as exc:
        raise AcademicPaperImportError(exc.error_type, exc.message) from exc
    except AcademicPaperImportError:
        raise
    except Exception as exc:
        logger.error("获取外部论文失败: exception_type=%s", type(exc).__name__)
        raise AcademicPaperImportError(
            "paper_import_fetch_failed", "获取外部论文元数据或公开 PDF 失败"
        ) from exc

    try:
        content_hash = await calculate_content_hash(pdf_bytes)
        if await knowledge_base.file_existed_in_db(kb_id, content_hash):
            raise AcademicPaperImportError("file_already_imported", "该 PDF 内容已经存在于当前知识库")
    except AcademicPaperImportError:
        raise
    except Exception as exc:
        logger.error("准备外部论文导入失败: exception_type=%s", type(exc).__name__)
        raise AcademicPaperImportError("paper_import_prepare_failed", "外部论文导入准备失败") from exc

    paper_id = str(paper["paperId"])
    filename = _safe_filename(paper_metadata["title"], paper_id)
    object_name = f"{kb_id}/upload/{filename.rsplit('.', 1)[0]}-{hashstr(content_hash, 10)}.pdf"
    minio_client = get_minio_client()
    try:
        upload_result = await minio_client.aupload_file(
            bucket_name=MinIOClient.KB_BUCKETS["documents"],
            object_name=object_name,
            data=pdf_bytes,
            content_type="application/pdf",
        )
    except Exception as exc:
        await _cleanup_failed_import(
            kb_id=kb_id,
            minio_client=minio_client,
            object_name=object_name,
        )
        logger.error("上传外部论文失败: exception_type=%s", type(exc).__name__)
        raise AcademicPaperImportError("paper_import_upload_failed", "外部论文 PDF 上传失败") from exc
    params = {
        "content_type": "file",
        "content_hashes": {upload_result.url: content_hash},
        "file_sizes": {upload_result.url: len(pdf_bytes)},
        "source_path": filename,
        "chunk_preset_id": "academic",
        "chunk_parser_config": {"paper_metadata": paper_metadata},
        "external_import": {
            "provider": "semantic_scholar",
            "identifier": paper_id,
            "source_url": final_url,
        },
    }
    try:
        file_meta = await knowledge_base.add_file_record(
            kb_id,
            upload_result.url,
            params=params,
            operator_id=str(current_user.uid),
        )
    except Exception as exc:
        await _cleanup_failed_import(
            kb_id=kb_id,
            minio_client=minio_client,
            object_name=object_name,
        )
        logger.error("创建外部论文文件记录失败: exception_type=%s", type(exc).__name__)
        raise AcademicPaperImportError("paper_import_record_failed", "外部论文文件记录创建失败") from exc

    try:
        task = await tasker.enqueue(
            name=f"导入学术论文 ({paper_metadata['title'][:220]})",
            task_type="academic_paper_import",
            payload={
                "kb_id": kb_id,
                "file_id": file_meta["file_id"],
                "paper_id": paper_metadata["paper_id"],
                "semantic_scholar_id": paper_id,
                "paper_metadata": paper_metadata,
                "operator_id": str(current_user.uid),
            },
            coroutine=_resume_external_paper_import_task,
        )
    except Exception as exc:
        await _cleanup_failed_import(
            kb_id=kb_id,
            minio_client=minio_client,
            object_name=object_name,
            file_id=str(file_meta["file_id"]),
        )
        logger.error("提交外部论文导入任务失败: exception_type=%s", type(exc).__name__)
        raise AcademicPaperImportError("paper_import_enqueue_failed", "外部论文导入任务提交失败") from exc

    return {
        "status": "queued",
        "task_id": task.id,
        "kb_id": kb_id,
        "file_id": file_meta["file_id"],
        "paper_id": paper_metadata["paper_id"],
        "semantic_scholar_id": paper_id,
        "title": paper_metadata["title"],
        "filename": filename,
    }


async def _resume_external_paper_import_task(context: TaskContext) -> dict[str, Any]:
    payload = context.payload
    try:
        kb_id = str(payload["kb_id"])
        file_id = str(payload["file_id"])
        paper_metadata = payload["paper_metadata"]
        operator_id = str(payload["operator_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("外部论文导入恢复参数无效") from exc
    if not kb_id or not file_id or not operator_id or not isinstance(paper_metadata, dict):
        raise ValueError("外部论文导入恢复参数无效")

    file_repo = KnowledgeFileRepository()
    await _ensure_import_owner_can_write(operator_id=operator_id, kb_id=kb_id, file_id=file_id)

    file_info = await knowledge_base.get_file_basic_info(kb_id, file_id)
    file_meta = file_info.get("meta") if isinstance(file_info, dict) else None
    if isinstance(file_meta, dict) and file_meta.get("status") == "parsing":
        await file_repo.update_fields(
            file_id=file_id,
            kb_id=kb_id,
            data={"status": "uploaded", "error_message": "服务重启后恢复外部论文解析"},
        )

    return await _run_import_task(
        context,
        kb_id=kb_id,
        file_id=file_id,
        paper_metadata=paper_metadata,
        operator_id=operator_id,
    )


async def recover_external_paper_imports() -> int:
    """Requeue external imports whose file record survived an API restart."""
    recovered = 0
    file_repo = KnowledgeFileRepository()
    for kb in await KnowledgeBaseRepository().get_all():
        for record in await file_repo.list_by_kb_id(str(kb.kb_id)):
            params = record.processing_params if isinstance(record.processing_params, dict) else {}
            if not isinstance(params.get("external_import"), dict):
                continue
            if record.status == "indexed":
                continue
            paper_config = params.get("chunk_parser_config")
            paper_metadata = paper_config.get("paper_metadata") if isinstance(paper_config, dict) else None
            if not isinstance(paper_metadata, dict) or not paper_metadata.get("paper_id"):
                await file_repo.update_fields(
                    file_id=record.file_id,
                    kb_id=record.kb_id,
                    data={"status": "failed", "error_message": "外部论文恢复缺少论文元数据"},
                )
                continue
            if record.status not in {
                "uploaded",
                "parsing",
                "error_parsing",
                "parsed",
                "error_indexing",
                "indexing",
                "failed",
            }:
                continue
            if record.status == "parsing":
                await file_repo.update_fields(
                    file_id=record.file_id,
                    kb_id=record.kb_id,
                    data={"status": "uploaded", "error_message": "服务重启后恢复外部论文解析"},
                )
            elif record.status == "indexing":
                await file_repo.update_fields(
                    file_id=record.file_id,
                    kb_id=record.kb_id,
                    data={"status": "error_indexing", "error_message": "服务重启后恢复外部论文索引"},
                )
            user = await UserRepository().get_by_uid(str(record.created_by or ""))
            if user is None or bool(user.is_deleted):
                await file_repo.update_fields(
                    file_id=record.file_id,
                    kb_id=record.kb_id,
                    data={"status": "failed", "error_message": "外部论文导入任务所有者不存在或已删除"},
                )
                continue
            try:
                await _ensure_access(user, str(record.kb_id), write=True)
            except HTTPException:
                await file_repo.update_fields(
                    file_id=record.file_id,
                    kb_id=record.kb_id,
                    data={"status": "failed", "error_message": "外部论文导入任务所有者已失去知识库权限"},
                )
                continue
            operator_id = str(user.uid)

            try:
                _, created = await tasker.enqueue_unique_by_payload(
                    name=f"恢复外部论文导入 ({record.filename})",
                    task_type="academic_paper_import",
                    payload={
                        "kb_id": record.kb_id,
                        "file_id": record.file_id,
                        "paper_id": paper_metadata["paper_id"],
                        "paper_metadata": paper_metadata,
                        "operator_id": operator_id,
                    },
                    payload_match={"kb_id": record.kb_id, "file_id": record.file_id},
                    statuses={"pending", "running"},
                    coroutine=_resume_external_paper_import_task,
                )
            except Exception as exc:
                logger.error(
                    "恢复外部论文导入任务入队失败: kb_id=%s file_id=%s exception_type=%s",
                    record.kb_id,
                    record.file_id,
                    type(exc).__name__,
                )
                await file_repo.update_fields(
                    file_id=record.file_id,
                    kb_id=record.kb_id,
                    data={"status": "failed", "error_message": "外部论文导入恢复任务提交失败"},
                )
                continue
            recovered += int(created)
    return recovered


tasker.register_resumable_handler("academic_paper_import", _resume_external_paper_import_task)


__all__ = [
    "AcademicPaperImportError",
    "import_external_paper",
    "recover_external_paper_imports",
    "search_external_papers",
]
