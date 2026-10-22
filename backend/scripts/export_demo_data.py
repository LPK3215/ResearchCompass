"""导出 ResearchCompass 演示数据为可随仓库分发的目录。

导出产物（默认写到仓库根目录的 ``demo_data/``）：

- ``manifest.json``：知识库信息、论文元数据、PDF 清单（文件名 / sha256 / 字节数 / 来源）
- ``papers/*.pdf``：原始 PDF 全文（开放获取论文）
- ``chunks.json``：每个 chunk 的正文与定位元数据（页码、章节、定位类型）
- ``business_data.json``：笔记、筛选、检索历史、证据综述、评测、研究项目等业务快照

每个知识库导出到 ``<导出根目录>/<kb_id>/`` 子目录，便于一次导出多个知识库。
脚本只读取数据并复制文件，不修改也不删除任何已有数据。
导入请使用 ``scripts/import_demo_data.py``。

用法::

    python scripts/export_demo_data.py --kb-id kb_xxx
    python scripts/export_demo_data.py --kb-id kb_xxx kb_yyy --output ../demo_data
    python scripts/export_demo_data.py --kb-id kb_xxx --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
for import_path in (APP_ROOT, APP_ROOT / "package", SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

DEFAULT_OUTPUT_DIR = REPO_ROOT / "demo_data"


def load_project_env() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    load_dotenv(REPO_ROOT / ".env", override=False)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")


def _jsonable(value: Any) -> Any:
    """把数据库取值转成可 JSON 序列化的形式。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _slugify(title: str) -> str:
    """生成稳定、可读且不含路径分隔符的文件名。"""
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", (title or "").strip().casefold()).strip("-")
    return (slug or "paper")[:80]


def _read_paper_bytes(source_path: str, *, dry_run: bool) -> bytes | None:
    """定位论文 PDF 原文；找不到时返回 ``None``。

    试运行只判定“是否可导出”，不真正下载也不落盘。
    """
    from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
    from yuxi.storage.minio.client import get_minio_client

    if is_minio_url(source_path):
        if dry_run:
            return b""
        bucket_name, object_name = parse_minio_url(source_path)
        return get_minio_client().download_file(bucket_name, object_name)
    local_path = Path(source_path)
    if not local_path.is_file():
        return None
    return b"" if dry_run else local_path.read_bytes()


async def _export_papers(
    session, kb_id: str, papers_dir: Path, *, dry_run: bool
) -> tuple[list[dict[str, Any]], set[str], list[dict[str, Any]]]:
    """导出论文元数据与 PDF 原文。

    返回 ``(论文条目, 可恢复的文件 ID 集合, 未能取到原文的文件清单)``。
    只有取到原文的文件才会被标记为可恢复——导入端按文件重建 chunk，
    没有原文的文件即使导出了 chunk 也无法在目标环境恢复。
    """
    from sqlalchemy import select
    from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
    from yuxi.storage.postgres.models_knowledge import AcademicPaper, KnowledgeFile

    paper_rows = list(
        (
            await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id).order_by(AcademicPaper.paper_id)
            )
        )
        .scalars()
        .all()
    )
    file_ids = [str(row.file_id) for row in paper_rows if row.file_id]
    file_rows = (
        {
            str(row.file_id): row
            for row in (await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id.in_(file_ids))))
            .scalars()
            .all()
        }
        if file_ids
        else {}
    )

    entries: list[dict[str, Any]] = []
    missing_files: list[dict[str, Any]] = []
    restorable_file_ids: set[str] = set()
    used_names: set[str] = set()
    for paper in paper_rows:
        file_row = file_rows.get(str(paper.file_id)) if paper.file_id else None
        entry: dict[str, Any] = {
            "paper_id": str(paper.paper_id),
            "source_row_id": int(paper.id),
            "title": str(paper.title),
            "abstract": paper.abstract,
            "authors": list(paper.authors or []),
            "publication_year": paper.publication_year,
            "venue": paper.venue,
            "doi": paper.doi,
            "keywords": list(paper.keywords or []),
            "language": paper.language,
            "external_ids": dict(paper.external_ids or {}),
            "citation_count": paper.citation_count,
            "metadata_source": paper.metadata_source,
            "metadata_status": paper.metadata_status,
            "metadata_revision": paper.metadata_revision,
            "indexed_revision": paper.indexed_revision,
            "source_file_name": file_row.filename if file_row else None,
        }

        source_path = (file_row.minio_url or file_row.path) if file_row else None
        data = _read_paper_bytes(source_path, dry_run=dry_run) if source_path else None
        if data is None:
            # 取不到原文的论文分两种：没有关联知识文件，或原文存放位置不可达。
            missing_files.append(
                {
                    "file_id": str(paper.file_id) if paper.file_id else None,
                    "filename": file_row.filename if file_row else None,
                    "paper_id": str(paper.paper_id),
                    "reason": "原文存放位置不可达" if source_path else "没有关联的知识文件",
                    "title": str(paper.title),
                }
            )
        else:
            pdf_name = f"{_slugify(paper.title)}.pdf"
            suffix = 1
            while pdf_name in used_names:
                suffix += 1
                pdf_name = f"{_slugify(paper.title)}-{suffix}.pdf"
            used_names.add(pdf_name)
            entry["pdf_file"] = f"papers/{pdf_name}"
            if not dry_run:
                papers_dir.mkdir(parents=True, exist_ok=True)
                (papers_dir / pdf_name).write_bytes(data)
                entry["pdf_sha256"] = hashlib.sha256(data).hexdigest()
                entry["pdf_bytes"] = len(data)
                if is_minio_url(source_path):
                    bucket_name, object_name = parse_minio_url(source_path)
                    entry["pdf_object"] = f"{bucket_name}/{object_name}"
            restorable_file_ids.add(str(paper.file_id))
        entries.append(entry)
    return entries, restorable_file_ids, missing_files


async def _export_chunks(
    session,
    kb_id: str,
    paper_entries: list[dict[str, Any]],
    restorable_file_ids: set[str],
) -> dict[str, Any]:
    """导出每个 chunk 的正文与定位元数据；论文快照在导入时按 manifest 回填。

    只导出可恢复文件的 chunk：导入端以 PDF 原文为锚点重建 chunk，
    取不到原文的文件即使导出正文也无法在目标环境恢复。
    """
    from demo_data_spec import CHUNK_ANALYSIS_FIELDS, REDUNDANT_CHUNK_METADATA_KEYS
    from sqlalchemy import select
    from yuxi.storage.postgres.models_knowledge import KnowledgeChunk

    file_to_paper = {
        str(entry["source_file_id"]): str(entry["paper_id"])
        for entry in paper_entries
        if entry.get("source_file_id") and str(entry["source_file_id"]) in restorable_file_ids
    }
    file_ids = list(file_to_paper)
    if not file_ids:
        return {}
    rows = list(
        (
            await session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.kb_id == kb_id, KnowledgeChunk.file_id.in_(file_ids))
                .order_by(KnowledgeChunk.file_id, KnowledgeChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    chunks: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        metadata = dict(row.chunk_metadata or {})
        for key in REDUNDANT_CHUNK_METADATA_KEYS:
            metadata.pop(key, None)
        record = {
            "chunk_id": str(row.chunk_id),
            "chunk_index": int(row.chunk_index),
            "content": row.content,
            "start_char_pos": row.start_char_pos,
            "end_char_pos": row.end_char_pos,
            "start_token_pos": row.start_token_pos,
            "end_token_pos": row.end_token_pos,
            "chunk_metadata": _jsonable(metadata),
        }
        for field in CHUNK_ANALYSIS_FIELDS:
            record[field] = _jsonable(getattr(row, field, None))
        chunks.setdefault(file_to_paper.get(str(row.file_id), ""), []).append(record)
    return chunks


async def _export_business_data(session, kb_id: str) -> dict[str, Any]:
    """按表导出业务数据快照；子表按父表的业务主键过滤，保持引用关系完整。"""
    from demo_data_spec import BUSINESS_KEY_COLUMN, BUSINESS_TABLES
    from sqlalchemy import select
    from yuxi.storage.postgres.models_knowledge import Base

    metadata = Base.metadata
    result: dict[str, Any] = {}
    parent_keys: dict[str, list[Any]] = {}
    for table_name, scope in BUSINESS_TABLES:
        table = metadata.tables.get(table_name)
        if table is None:
            continue
        if scope == "kb":
            statement = select(table).where(table.c.kb_id == kb_id)
        elif isinstance(scope, tuple) and scope[0] == "kb":
            statement = select(table).where(table.c[scope[1]] == kb_id)
        else:
            parent_table, parent_column = scope
            parent_values = parent_keys.get(parent_table) or []
            if not parent_values:
                continue
            statement = select(table).where(table.c[parent_column].in_(parent_values))
        rows = (await session.execute(statement)).mappings().all()
        exported_rows = [{key: _jsonable(value) for key, value in dict(row).items()} for row in rows]
        result[table_name] = exported_rows
        key_column = BUSINESS_KEY_COLUMN.get(table_name)
        if key_column is not None:
            parent_keys[table_name] = [row[key_column] for row in exported_rows if row.get(key_column) is not None]
    return result


async def _export_one_kb(session, kb_id: str, bundle_dir: Path, *, dry_run: bool) -> dict[str, Any]:
    """导出一个知识库的完整快照，返回 manifest。"""
    from sqlalchemy import select
    from yuxi.storage.postgres.models_knowledge import AcademicPaper, KnowledgeBase

    kb_row = (await session.execute(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))).scalar_one_or_none()
    if kb_row is None:
        raise RuntimeError(f"知识库不存在：{kb_id}")

    paper_rows = list(
        (
            await session.execute(
                select(AcademicPaper).where(AcademicPaper.kb_id == kb_id).order_by(AcademicPaper.paper_id)
            )
        )
        .scalars()
        .all()
    )
    file_id_by_paper = {str(row.paper_id): str(row.file_id) for row in paper_rows if row.file_id}

    paper_entries, restorable_file_ids, missing_files = await _export_papers(
        session, kb_id, bundle_dir / "papers", dry_run=dry_run
    )
    for entry in paper_entries:
        entry["source_file_id"] = file_id_by_paper.get(str(entry["paper_id"]))

    chunks = await _export_chunks(session, kb_id, paper_entries, restorable_file_ids)
    business_data = await _export_business_data(session, kb_id)

    chunk_count_by_paper = {paper_id: len(items) for paper_id, items in chunks.items()}
    for entry in paper_entries:
        entry["chunk_count"] = chunk_count_by_paper.get(str(entry["paper_id"]), 0)
        entry.pop("source_file_id", None)

    manifest = {
        "schema_version": 2,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_kb": {
            "kb_id": kb_id,
            "name": kb_row.name,
            "description": kb_row.description,
            "kb_type": kb_row.kb_type,
            "embedding_model_spec": kb_row.embedding_model_spec,
            "llm_model_spec": kb_row.llm_model_spec,
            "query_params": _jsonable(kb_row.query_params),
            "mindmap": _jsonable(kb_row.mindmap),
        },
        "papers": paper_entries,
        "papers_without_pdf": missing_files,
        "counts": {
            "papers": len(paper_entries),
            "papers_with_pdf": sum(1 for entry in paper_entries if entry.get("pdf_file")),
            "chunks": sum(len(items) for items in chunks.values()),
            "papers_without_pdf": len(missing_files),
            **{table: len(rows) for table, rows in business_data.items()},
        },
    }

    if not dry_run:
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (bundle_dir / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        (bundle_dir / "business_data.json").write_text(
            json.dumps(business_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return manifest


async def export(*, kb_ids: list[str], output_dir: Path, dry_run: bool) -> int:
    from yuxi.storage.postgres.manager import pg_manager

    try:
        pg_manager.initialize()
        mode = "试运行" if dry_run else "已导出"
        async with pg_manager.get_async_session_context() as session:
            for kb_id in kb_ids:
                bundle_dir = output_dir / kb_id
                manifest = await _export_one_kb(session, kb_id, bundle_dir, dry_run=dry_run)
                counts = manifest["counts"]
                print(
                    f"{mode}完成：知识库《{manifest['source_kb']['name']}》→ {bundle_dir}\n"
                    f"  论文 {counts['papers']} 篇（其中带 PDF {counts['papers_with_pdf']} 篇）"
                    f"，chunk {counts['chunks']} 个"
                )
                for table, count in counts.items():
                    if table in {"papers", "papers_with_pdf", "chunks", "papers_without_pdf"}:
                        continue
                    if count:
                        print(f"  业务表 {table}: {count} 行")
                missing_files = manifest.get("papers_without_pdf") or []
                if missing_files:
                    print(f"  警告：{len(missing_files)} 篇论文没有可恢复的原文，其 chunk 未导出：")
                    for item in missing_files:
                        print(
                            f"    - {item.get('title') or item.get('filename') or item.get('paper_id')}"
                            f"（{item.get('reason')}）"
                        )
        return 0
    finally:
        await pg_manager.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 ResearchCompass 演示数据（语料 + 业务数据）")
    parser.add_argument("--kb-id", required=True, nargs="+", help="要导出的知识库 kb_id，可指定多个")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="导出根目录，默认仓库根目录 demo_data/")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = parser.parse_args()

    load_project_env()
    try:
        return asyncio.run(
            export(kb_ids=list(args.kb_id), output_dir=Path(args.output).resolve(), dry_run=args.dry_run)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"演示数据导出失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
