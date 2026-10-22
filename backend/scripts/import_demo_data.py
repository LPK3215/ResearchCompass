"""把 ResearchCompass 演示数据导入到当前环境。

与 ``scripts/export_demo_data.py`` 配套：导出脚本把"体验验证版本"的语料、
chunk 中间结果与业务快照固化到 ``demo_data/``，本脚本负责在任意一个环境里
把它们恢复回来，让新环境启动后就能看到与导出时一致的内容。

导入只做新增，不删除也不覆盖目标环境已有数据：

1. 目标知识库：``--kb-id`` 指定已有知识库；未指定时按 manifest 新建一个。
2. 论文 PDF：上传到 MinIO 并登记为知识文件，状态直接置为「已索引」。
3. chunk：正文与页码定位元数据按导出快照原样重建，向量用当前环境的 embedding 模型重新计算，
   因此不依赖导出时的解析与解析容器。
4. 业务快照：笔记、筛选、标签、检索历史、证据、评测、研究项目等按主键重新映射后写入。

用法::

    python scripts/import_demo_data.py
    python scripts/import_demo_data.py --data-dir ../demo_data --kb-name "演示知识库"
    python scripts/import_demo_data.py --kb-id kb_xxx --owner zwj
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
import traceback
import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
for import_path in (APP_ROOT, APP_ROOT / "package", SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

DEFAULT_DATA_DIR = REPO_ROOT / "demo_data"


def load_project_env() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    load_dotenv(REPO_ROOT / ".env", override=False)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise RuntimeError(f"演示数据文件缺失：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _paper_snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    """按 manifest 还原 chunk_metadata["paper"] 快照。"""
    from demo_data_spec import PAPER_METADATA_KEYS

    return {key: entry[key] for key in PAPER_METADATA_KEYS if key in entry}


def _build_chunk_records(
    file_id: str, filename: str, chunk_rows: list[dict[str, Any]], paper_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """把导出的 chunk 快照还原成写入向量库所需的记录结构。"""
    records: list[dict[str, Any]] = []
    for row in chunk_rows:
        index = int(row["chunk_index"])
        chunk_id = f"{file_id}_chunk_{index}"
        metadata = dict(row.get("chunk_metadata") or {})
        if paper_snapshot:
            metadata["paper"] = paper_snapshot
        records.append(
            {
                "id": chunk_id,
                "content": row["content"],
                "file_id": file_id,
                "filename": filename,
                "chunk_index": index,
                "source": filename,
                "chunk_id": chunk_id,
                "start_char_pos": row.get("start_char_pos"),
                "end_char_pos": row.get("end_char_pos"),
                "start_token_pos": row.get("start_token_pos"),
                "end_token_pos": row.get("end_token_pos"),
                "chunk_metadata": metadata,
                "extraction_result": row.get("extraction_result"),
            }
        )
    return records


async def _resolve_owner(owner_uid: str | None) -> str:
    """确定导入者账号；系统还没有用户时按标准种子用户初始化。"""
    from sqlalchemy import func, select
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import User

    async with pg_manager.get_async_session_context() as session:
        user_count = await session.scalar(select(func.count(User.id)))

    if not user_count:
        from seed_initial_users import seed_initial_users

        print("系统尚无用户，正在初始化标准种子用户……")
        await seed_initial_users()

    async with pg_manager.get_async_session_context() as session:
        if owner_uid:
            exists = await session.scalar(select(User.id).where(User.uid == owner_uid))
            if not exists:
                raise RuntimeError(f"用户不存在：{owner_uid}")
            return owner_uid
        candidate = await session.scalar(select(User).where(User.role.in_(("superadmin", "admin"))).order_by(User.id))
        if candidate is None:
            raise RuntimeError("系统内没有可用的管理员账号，请用 --owner 指定导入者")
        return str(candidate.uid)


async def _resolve_embedding_spec(preferred: str | None) -> str | None:
    """优先沿用导出时的 embedding 模型；目标环境没有该模型时退回可用模型。"""
    from yuxi.models.providers.cache import model_cache

    if preferred:
        info = model_cache.get_model_info(preferred)
        if info and info.model_type == "embedding":
            return preferred
    candidates = model_cache.get_all_specs("embedding")
    return candidates[0].spec if candidates else None


async def _restore_kb_settings(*, kb_instance: Any, kb_id: str, source: dict[str, Any]) -> None:
    """把导出时的检索参数与思维导图恢复到新建的知识库，保证演示行为与源环境一致。"""
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

    updates: dict[str, Any] = {}
    query_params = source.get("query_params")
    if query_params:
        updates["query_params"] = query_params
        kb_instance.databases_meta.setdefault(kb_id, {})["query_params"] = query_params
    mindmap = source.get("mindmap")
    if mindmap:
        updates["mindmap"] = mindmap
    if updates:
        await KnowledgeBaseRepository().update(kb_id, updates)


async def _resolve_target_kb(
    *, kb_id: str | None, kb_name: str | None, manifest: dict[str, Any], owner_uid: str
) -> tuple[str, Any, str]:
    """返回 ``(kb_id, 知识库实例, embedding 模型 spec)``。"""
    from yuxi.knowledge.runtime import knowledge_base

    source = manifest.get("source_kb") or {}

    if kb_id:
        kb_instance = await knowledge_base._get_kb_for_database(kb_id)
        await kb_instance._load_metadata()
        spec = kb_instance.databases_meta[kb_id].get("embedding_model_spec")
        return kb_id, kb_instance, spec

    base_name = kb_name or f"{source.get('name') or '演示知识库'}（演示数据）"
    name = base_name
    suffix = 1
    while await knowledge_base.database_name_exists(name):
        suffix += 1
        name = f"{base_name} {suffix}"

    embedding_spec = await _resolve_embedding_spec(source.get("embedding_model_spec"))
    db_info = await knowledge_base.create_database(
        name,
        source.get("description") or "由演示数据导入脚本创建的示例知识库",
        kb_type=source.get("kb_type") or "milvus",
        embedding_model_spec=embedding_spec,
        llm_model_spec=source.get("llm_model_spec"),
        created_by=owner_uid,
    )
    new_kb_id = str(db_info["kb_id"])
    kb_instance = await knowledge_base._get_kb_for_database(new_kb_id)
    await _restore_kb_settings(kb_instance=kb_instance, kb_id=new_kb_id, source=source)
    print(f"已创建演示知识库《{name}》（{new_kb_id}），embedding 模型：{embedding_spec}")
    return new_kb_id, kb_instance, embedding_spec


async def _upload_paper_file(
    *, kb_instance: Any, kb_id: str, entry: dict[str, Any], pdf_path: Path, owner_uid: str
) -> tuple[str, str]:
    """上传 PDF 并登记知识文件，返回 ``(file_id, 文件名)``。"""
    from yuxi.knowledge.utils.kb_utils import calculate_content_hash
    from yuxi.storage.minio.client import MinIOClient, aupload_file_to_minio

    data = pdf_path.read_bytes()
    content_hash = await calculate_content_hash(data)
    filename = entry.get("source_file_name") or pdf_path.name
    basename = Path(filename).stem
    ext = Path(filename).suffix or ".pdf"
    object_name = f"{kb_id}/upload/{basename}_{int(time.time() * 1000)}{ext}"
    bucket_name = MinIOClient.KB_BUCKETS["documents"]
    minio_url = await aupload_file_to_minio(bucket_name, object_name, data)

    file_meta = await kb_instance.add_file_record(
        kb_id,
        minio_url,
        params={
            "content_type": "file",
            "content_hashes": {minio_url: content_hash},
            "file_sizes": {minio_url: len(data)},
            "source_path": filename,
        },
        operator_id=owner_uid,
    )
    return str(file_meta["file_id"]), filename


async def _mark_file_indexed(*, file_id: str, kb_id: str, chunk_count: int, token_count: int) -> None:
    """导入的 chunk 不经过解析与索引流水线，直接把文件状态置为已索引。"""
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    await KnowledgeFileRepository().update_fields(
        file_id=file_id,
        kb_id=kb_id,
        data={
            "status": "indexed",
            "error_message": None,
            "chunk_count": chunk_count,
            "token_count": token_count,
        },
    )


async def _write_chunks(
    *,
    kb_instance: Any,
    kb_id: str,
    file_id: str,
    filename: str,
    chunk_rows: list[dict[str, Any]],
    paper_snapshot: dict[str, Any],
    embedding_spec: str,
) -> dict[str, str]:
    """写入 chunk（PostgreSQL + Milvus），返回 ``旧 chunk_id -> 新 chunk_id`` 映射。"""
    from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens

    if not chunk_rows:
        return {}

    records = _build_chunk_records(file_id, filename, chunk_rows, paper_snapshot)
    collection = await kb_instance._get_milvus_collection(kb_id)
    if not collection:
        raise RuntimeError(f"知识库 {kb_id} 的向量集合不可用")
    embedding_function = kb_instance._get_embedding_function(embedding_spec)
    await kb_instance._embed_and_store_chunks(kb_id, file_id, collection, records, embedding_function)

    token_count = sum(count_tokens(record["content"]) for record in records)
    await _mark_file_indexed(
        file_id=file_id,
        kb_id=kb_id,
        chunk_count=len(records),
        token_count=token_count,
    )
    return {str(row["chunk_id"]): record["chunk_id"] for row, record in zip(chunk_rows, records, strict=True)}


async def _upsert_paper(*, kb_id: str, file_id: str | None, entry: dict[str, Any]) -> int:
    """写入学术论文记录，返回目标环境中的 ``academic_papers.id``。"""
    from yuxi.repositories.academic_paper_repository import AcademicPaperRepository

    data = {
        "paper_id": str(entry["paper_id"]),
        "title": str(entry["title"]),
        "abstract": entry.get("abstract"),
        "authors": list(entry.get("authors") or []),
        "publication_year": entry.get("publication_year"),
        "venue": entry.get("venue"),
        "doi": entry.get("doi"),
        "keywords": list(entry.get("keywords") or []),
        "language": entry.get("language"),
        "external_ids": dict(entry.get("external_ids") or {}),
        "citation_count": entry.get("citation_count"),
        "metadata_source": entry.get("metadata_source") or "document",
        "metadata_status": entry.get("metadata_status") or "extracted",
    }

    if file_id:
        paper = await AcademicPaperRepository().upsert_for_file(kb_id=kb_id, file_id=file_id, data=data)
        return int(paper.id)

    from sqlalchemy import select
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_knowledge import AcademicPaper

    async with pg_manager.get_async_session_context() as session:
        existing = await session.scalar(
            select(AcademicPaper).where(AcademicPaper.kb_id == kb_id, AcademicPaper.paper_id == data["paper_id"])
        )
        if existing is not None:
            return int(existing.id)
        paper = AcademicPaper(kb_id=kb_id, file_id=None, **data)
        session.add(paper)
        await session.flush()
        return int(paper.id)


async def _import_papers(
    *,
    kb_instance: Any,
    kb_id: str,
    manifest: dict[str, Any],
    chunks_by_paper: dict[str, list[dict[str, Any]]],
    data_dir: Path,
    owner_uid: str,
    embedding_spec: str,
    with_chunks: bool,
) -> tuple[dict[str, str], dict[int, int], dict[str, str]]:
    """导入论文、PDF 与 chunk，返回三张主键映射表。"""
    paper_ids: dict[str, str] = {}
    paper_rows: dict[int, int] = {}
    chunk_ids: dict[str, str] = {}

    for index, entry in enumerate(manifest.get("papers") or [], 1):
        paper_id = str(entry["paper_id"])
        pdf_rel = entry.get("pdf_file")
        pdf_path = (data_dir / pdf_rel) if pdf_rel else None
        file_id: str | None = None

        if pdf_path is not None and pdf_path.exists():
            file_id, filename = await _upload_paper_file(
                kb_instance=kb_instance,
                kb_id=kb_id,
                entry=entry,
                pdf_path=pdf_path,
                owner_uid=owner_uid,
            )
            chunk_rows = chunks_by_paper.get(paper_id) or []
            if with_chunks and chunk_rows:
                chunk_ids.update(
                    await _write_chunks(
                        kb_instance=kb_instance,
                        kb_id=kb_id,
                        file_id=file_id,
                        filename=filename,
                        chunk_rows=chunk_rows,
                        paper_snapshot=_paper_snapshot(entry),
                        embedding_spec=embedding_spec,
                    )
                )
        else:
            chunk_rows = []

        row_id = await _upsert_paper(kb_id=kb_id, file_id=file_id, entry=entry)
        paper_ids[paper_id] = paper_id
        source_row_id = entry.get("source_row_id")
        if source_row_id is not None:
            paper_rows[int(source_row_id)] = row_id
        print(f"  [{index}/{len(manifest.get('papers') or [])}] 论文《{entry['title'][:40]}》已导入")

    return paper_ids, paper_rows, chunk_ids


async def _plan_business_keys(session, business: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, str]]:
    """为每张表的业务主键生成目标环境里的新值（与已有记录冲突时换新）。"""
    from demo_data_spec import BUSINESS_KEY_COLUMN
    from sqlalchemy import select
    from yuxi.storage.postgres.models_knowledge import Base

    plan: dict[str, dict[str, str]] = {}
    for table_name, rows in business.items():
        key_column = BUSINESS_KEY_COLUMN.get(table_name)
        if not key_column or not rows:
            continue
        table = Base.metadata.tables[table_name]
        existing = set((await session.execute(select(table.c[key_column]))).scalars().all())
        mapping: dict[str, str] = {}
        for row in rows:
            old_key = row.get(key_column)
            if old_key is None:
                continue
            old_key = str(old_key)
            new_key = old_key
            if new_key in existing:
                new_key = f"{old_key[:48]}-{uuid.uuid4().hex[:8]}"
            existing.add(new_key)
            mapping[old_key] = new_key
        plan[table_name] = mapping
    return plan


def _order_parents_first(rows: list[dict[str, Any]], key_column: str, parent_column: str) -> list[dict[str, Any]]:
    """自引用外键的父行必须先写入，否则外键校验失败。"""
    by_key = {str(row.get(key_column)): row for row in rows}
    depth_cache: dict[str, int] = {}

    def depth(row: dict[str, Any], visiting: frozenset[str]) -> int:
        key = str(row.get(key_column))
        if key in depth_cache:
            return depth_cache[key]
        if key in visiting:
            return 0
        parent = row.get(parent_column)
        parent_row = by_key.get(str(parent)) if parent else None
        value = 0 if parent_row is None else 1 + depth(parent_row, visiting | {key})
        depth_cache[key] = value
        return value

    return sorted(rows, key=lambda row: depth(row, frozenset()))


def _apply_column_remap(
    values: dict[str, Any],
    remap: dict[str, str],
    resolve: Callable[[str, Any], Any],
    nullable: dict[str, bool],
) -> tuple[dict[str, Any], int, bool]:
    """按重映射约定改写一行里的跨表引用。

    返回 ``(改写后的取值, 未能解析的引用数, 是否需要整行跳过)``。
    原值为空的外键保持为空：空引用是合法状态，不算「未能解析」。
    引用无法映射时，允许为空的列置空并计数，不允许为空的列需要整行跳过。
    """
    unresolved_count = 0
    for column_name, target in remap.items():
        if column_name not in values or values[column_name] is None:
            continue
        resolved = resolve(target, values[column_name])
        if resolved is None:
            if not nullable.get(column_name, True):
                return values, unresolved_count, True
            unresolved_count += 1
        values[column_name] = resolved
    return values, unresolved_count, False


async def _import_business_data(
    *,
    kb_id: str,
    business: dict[str, list[dict[str, Any]]],
    owner_uid: str,
    paper_ids: dict[str, str],
    paper_rows: dict[int, int],
    chunk_ids: dict[str, str],
    key_plan: dict[str, dict[str, str]],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """写入业务快照。

    返回 ``(已写入行数, 跳过行数, 未能解析引用的行数)``，均按表统计。
    写入使用 ``ON CONFLICT DO NOTHING``：重复导入同一个包不会报错，
    已存在的行按“已存在”跳过，不影响其余表的写入。
    """
    from demo_data_spec import BUSINESS_KEY_COLUMN, COLUMN_REMAP, REMAP_KEY_PLAN_TARGETS, SELF_REFERENCE_PARENT_COLUMN
    from sqlalchemy import Date, DateTime
    from sqlalchemy.dialects.postgresql import insert
    from yuxi.storage.postgres.column_types import UTCDateTime
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_knowledge import Base

    def _resolve(target: str, value: Any) -> Any:
        if target == "kb":
            return kb_id
        if target == "owner":
            return owner_uid
        if target == "token":
            return secrets.token_hex(32)
        if target == "paper":
            return paper_ids.get(str(value))
        if target == "paper_row":
            return paper_rows.get(int(value)) if value is not None else None
        if target == "chunk":
            return chunk_ids.get(str(value))
        table_name = REMAP_KEY_PLAN_TARGETS.get(target)
        if table_name is None:
            raise ValueError(f"未知的映射目标：{target}")
        return key_plan.get(table_name, {}).get(str(value))

    written: dict[str, int] = {}
    skipped: dict[str, int] = {}
    unresolved: dict[str, int] = {}
    async with pg_manager.get_async_session_context() as session:
        for table_name, rows in business.items():
            if not rows:
                continue
            table = Base.metadata.tables.get(table_name)
            if table is None:
                continue
            remap = COLUMN_REMAP.get(table_name, {})
            key_column = BUSINESS_KEY_COLUMN.get(table_name)
            column_names = {column.name for column in table.columns}
            datetime_columns = {
                column.name for column in table.columns if isinstance(column.type, (DateTime, UTCDateTime))
            }
            date_columns = {column.name for column in table.columns if isinstance(column.type, Date)}
            parent_column = SELF_REFERENCE_PARENT_COLUMN.get(table_name)
            if parent_column and key_column:
                rows = _order_parents_first(rows, key_column, parent_column)
            count = 0
            skipped_count = 0
            unresolved_count = 0
            for row in rows:
                values = {key: value for key, value in row.items() if key in column_names and key != "id"}
                if key_column and key_column in values:
                    values[key_column] = key_plan.get(table_name, {}).get(str(values[key_column]), values[key_column])
                for column_name in datetime_columns:
                    if isinstance(values.get(column_name), str):
                        values[column_name] = datetime.fromisoformat(values[column_name])
                for column_name in date_columns:
                    if isinstance(values.get(column_name), str):
                        values[column_name] = date.fromisoformat(values[column_name])
                values, unresolved_refs, skip = _apply_column_remap(
                    values,
                    remap,
                    _resolve,
                    {column.name: bool(column.nullable) for column in table.columns},
                )
                unresolved_count += unresolved_refs
                if skip:
                    print(f"  跳过 {table_name} 记录：存在无法映射到目标环境的必填引用")
                    skipped_count += 1
                    continue
                result = await session.execute(insert(table).values(**values).on_conflict_do_nothing())
                if result.rowcount:
                    count += 1
                else:
                    skipped_count += 1
            written[table_name] = count
            if skipped_count:
                skipped[table_name] = skipped_count
            if unresolved_count:
                unresolved[table_name] = unresolved_count
    return written, skipped, unresolved


def _discover_bundles(data_dir: Path) -> list[Path]:
    """找到演示数据目录下的所有知识库快照目录。"""
    if (data_dir / "manifest.json").exists():
        return [data_dir]
    bundles = sorted(path for path in data_dir.iterdir() if path.is_dir() and (path / "manifest.json").exists())
    if not bundles:
        raise RuntimeError(f"演示数据目录中没有找到 manifest.json：{data_dir}")
    return bundles


async def _import_bundle(
    *,
    bundle_dir: Path,
    kb_id: str | None,
    kb_name: str | None,
    owner_uid: str,
    with_chunks: bool,
    with_business: bool,
) -> dict[str, Any]:
    """导入一个知识库快照。"""
    from yuxi.storage.postgres.manager import pg_manager

    manifest = _read_json(bundle_dir / "manifest.json")
    chunks_by_paper = _read_json(bundle_dir / "chunks.json") if with_chunks else {}
    business = _read_json(bundle_dir / "business_data.json") if with_business else {}

    target_kb_id, kb_instance, embedding_spec = await _resolve_target_kb(
        kb_id=kb_id,
        kb_name=kb_name,
        manifest=manifest,
        owner_uid=owner_uid,
    )
    if not embedding_spec:
        raise RuntimeError("当前环境没有可用的 embedding 模型，无法重建向量数据")

    print(f"导入论文与 chunk 到知识库 {target_kb_id}……")
    paper_ids, paper_rows, chunk_ids = await _import_papers(
        kb_instance=kb_instance,
        kb_id=target_kb_id,
        manifest=manifest,
        chunks_by_paper=chunks_by_paper,
        data_dir=bundle_dir,
        owner_uid=owner_uid,
        embedding_spec=embedding_spec,
        with_chunks=with_chunks,
    )

    written: dict[str, int] = {}
    skipped: dict[str, int] = {}
    unresolved: dict[str, int] = {}
    if business:
        print("导入业务快照……")
        async with pg_manager.get_async_session_context() as session:
            key_plan = await _plan_business_keys(session, business)
        written, skipped, unresolved = await _import_business_data(
            kb_id=target_kb_id,
            business=business,
            owner_uid=owner_uid,
            paper_ids=paper_ids,
            paper_rows=paper_rows,
            chunk_ids=chunk_ids,
            key_plan=key_plan,
        )

    return {
        "kb_id": target_kb_id,
        "name": manifest.get("source_kb", {}).get("name", ""),
        "papers": len(paper_ids),
        "chunks": len(chunk_ids),
        "expected": manifest.get("counts", {}),
        "business": written,
        "business_skipped": skipped,
        "business_unresolved": unresolved,
    }


async def import_demo(
    *,
    data_dir: Path,
    kb_id: str | None,
    kb_name: str | None,
    owner_uid: str | None,
    with_chunks: bool,
    with_business: bool,
) -> list[dict[str, Any]]:
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    try:
        owner = await _resolve_owner(owner_uid)
        bundles = _discover_bundles(data_dir)
        summaries: list[dict[str, Any]] = []
        for bundle_dir in bundles:
            print(f"===== 导入快照 {bundle_dir.name} =====")
            summaries.append(
                await _import_bundle(
                    bundle_dir=bundle_dir,
                    kb_id=kb_id,
                    kb_name=kb_name,
                    owner_uid=owner,
                    with_chunks=with_chunks,
                    with_business=with_business,
                )
            )
        return summaries
    finally:
        await pg_manager.close()


def _compare_expected(expected: dict[str, Any], written: dict[str, int]) -> list[tuple[str, int, int]]:
    """对比导出快照的行数与实际写入行数，返回不一致的 ``(表名, 期望, 实际)``。"""
    mismatches: list[tuple[str, int, int]] = []
    for table, expected_count in expected.items():
        if table in {"papers", "papers_with_pdf", "chunks", "papers_without_pdf"}:
            continue
        actual = written.get(table, 0)
        if int(expected_count) != actual:
            mismatches.append((table, int(expected_count), actual))
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 ResearchCompass 演示数据（语料 + 业务快照）")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="演示数据目录，默认仓库根目录 demo_data/")
    parser.add_argument("--kb-id", default=None, help="导入到指定知识库；不指定则新建一个演示知识库")
    parser.add_argument("--kb-name", default=None, help="新建知识库时使用的名称")
    parser.add_argument("--owner", default=None, help="导入者账号 uid，默认取系统内第一个管理员")
    parser.add_argument("--no-chunks", action="store_true", help="只导入论文与业务快照，不重建 chunk")
    parser.add_argument("--no-business", action="store_true", help="只导入论文与 chunk，不导入业务快照")
    args = parser.parse_args()

    load_project_env()
    try:
        summaries = asyncio.run(
            import_demo(
                data_dir=Path(args.data_dir).resolve(),
                kb_id=args.kb_id,
                kb_name=args.kb_name,
                owner_uid=args.owner,
                with_chunks=not args.no_chunks,
                with_business=not args.no_business,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"演示数据导入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    mismatched = False
    for summary in summaries:
        print(
            f"导入完成：知识库《{summary['name']}》（{summary['kb_id']}），"
            f"论文 {summary['papers']} 篇，chunk {summary['chunks']} 个"
        )
        for table, count in summary["business"].items():
            if count:
                print(f"  业务表 {table}: {count} 行")
        for table, count in summary["business_skipped"].items():
            print(f"  业务表 {table}: 跳过 {count} 行（已存在或无法映射）")
        for table, count in summary["business_unresolved"].items():
            print(f"  业务表 {table}: {count} 行的引用未能在目标环境解析，对应字段已置空")
        differences = _compare_expected(summary["expected"], summary["business"])
        if differences:
            mismatched = True
            print("  行数与导出快照不一致：")
            for table, expected_count, actual in differences:
                print(f"    - {table}: 期望 {expected_count} 行，实际 {actual} 行")
        else:
            print("  行数与导出快照一致")
    return 1 if mismatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
