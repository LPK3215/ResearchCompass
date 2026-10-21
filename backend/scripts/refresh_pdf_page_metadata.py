"""重新计算已入库学术 PDF 的页码定位元数据。

解析/分块阶段写入的 ``source_page_start`` / ``source_page_end`` / ``source_rects``
只在入库时生成一次；页码锚点策略升级后，历史文件需要补算。本脚本只重算这些
定位字段并写回 ``knowledge_chunks.chunk_metadata``，不重新解析、不重建向量、
不删除任何 chunk。

用法::

    python scripts/refresh_pdf_page_metadata.py --dry-run
    python scripts/refresh_pdf_page_metadata.py --kb-id kb_xxx
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1]
for import_path in (APP_ROOT, APP_ROOT / "package"):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

PAGE_METADATA_KEYS = ("source_page_start", "source_page_end", "source_rects", "locator_type")


def load_project_env() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _page_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {}
    return {key: metadata.get(key) for key in PAGE_METADATA_KEYS}


def _source_path(file_row: Any) -> str | None:
    return file_row.minio_url or file_row.path


async def refresh(*, kb_id: str | None, dry_run: bool) -> int:
    from sqlalchemy import select

    from yuxi.knowledge.chunking.pdf_locator import attach_pdf_page_ranges
    from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_knowledge import KnowledgeFile

    chunk_repo = KnowledgeChunkRepository()
    try:
        pg_manager.initialize()
        async with pg_manager.get_async_session_context() as session:
            file_query = select(KnowledgeFile)
            if kb_id:
                file_query = file_query.where(KnowledgeFile.kb_id == kb_id)
            file_rows = list((await session.execute(file_query)).scalars().all())

        files_by_id = {row.file_id: row for row in file_rows}
        chunk_rows = await chunk_repo.list_by_file_ids(list(files_by_id))
        chunks_by_file: dict[str, list[Any]] = {}
        for chunk_row in chunk_rows:
            if (chunk_row.chunk_metadata or {}).get("document_type") != "academic_paper":
                continue
            chunks_by_file.setdefault(chunk_row.file_id, []).append(chunk_row)

        updated_files = 0
        updated_chunks = 0
        before_start = 0
        after_start = 0
        for file_id, file_chunks in sorted(chunks_by_file.items()):
            file_row = files_by_id.get(file_id)
            source_path = _source_path(file_row) if file_row else None
            if not source_path or not source_path.casefold().endswith(".pdf"):
                continue
            file_chunks.sort(key=lambda chunk: chunk.chunk_index)
            payload = [
                {"content": chunk.content or "", "chunk_metadata": dict(chunk.chunk_metadata or {})}
                for chunk in file_chunks
            ]
            try:
                attach_pdf_page_ranges(payload, source_path)
            except Exception as exc:  # noqa: BLE001
                print(f"[跳过] {file_id}：页码定位失败（{type(exc).__name__}）")
                continue

            updates: list[dict[str, Any]] = []
            for chunk_row, chunk in zip(file_chunks, payload, strict=True):
                old_metadata = chunk_row.chunk_metadata or {}
                new_metadata = chunk["chunk_metadata"]
                before_start += 1 if old_metadata.get("source_page_start") else 0
                after_start += 1 if new_metadata.get("source_page_start") else 0
                if _page_metadata(old_metadata) == _page_metadata(new_metadata):
                    continue
                merged = {**old_metadata, **new_metadata}
                updates.append({"chunk_id": chunk_row.chunk_id, "chunk_metadata": merged})
            if not updates:
                continue
            updated_files += 1
            updated_chunks += len(updates)
            if not dry_run:
                await chunk_repo.batch_upsert(updates)
            print(
                f"[更新] {file_id}（{file_row.filename if file_row else '?'}）："
                f"{len(updates)}/{len(file_chunks)} 个 chunk 的页码元数据变化"
            )

        mode = "试运行" if dry_run else "已写回"
        print(
            f"{mode}完成：扫描 {len(chunks_by_file)} 个学术文件，"
            f"更新 {updated_files} 个文件 / {updated_chunks} 个 chunk；"
            f"带起始页的 chunk {before_start} → {after_start}"
        )
        return 0
    finally:
        await pg_manager.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="重新计算已入库学术 PDF 的页码定位元数据")
    parser.add_argument("--kb-id", default=None, help="只处理指定知识库")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写回")
    args = parser.parse_args()

    load_project_env()
    try:
        return asyncio.run(refresh(kb_id=args.kb_id, dry_run=args.dry_run))
    except Exception as exc:  # noqa: BLE001
        print(f"页码元数据刷新失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
