"""校验演示数据包的导入结果。

对已导入到当前环境的演示知识库逐项核对：

1. **计数核对**：论文 / 文件 / chunk / 每张业务表的行数与导出快照一致；
2. **文件与 chunk 状态**：文件已索引，文件登记的 chunk 数与实际 chunk 行数一致；
3. **chunk 元数据**：页码定位字段、论文快照与 chunk 级分析结果已按快照回填；
4. **跨表引用**：按 ``scripts/demo_data_spec.py`` 的共享约定逐列检查孤儿引用；
5. **真实检索**：对导入后的知识库跑一次实际检索，确认向量数据可用。

脚本只读，不修改任何数据；全部检查通过时退出码为 0，否则为 1。

用法::

    python scripts/verify_demo_data.py --kb-id kb_xxx
    python scripts/verify_demo_data.py --kb-id kb_new --bundle kb_source
    python scripts/verify_demo_data.py --kb-id kb_xxx --query "检索式" --top-k 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
DEFAULT_QUERY = "检索增强生成的基本原理与典型方法有哪些？"
PAGE_LOCATOR_KEYS = ("source_page_start", "source_rects", "locator_type")


def load_project_env() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    load_dotenv(REPO_ROOT / ".env", override=False)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise RuntimeError(f"演示数据文件缺失：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


class _Report:
    """收集校验结果，最后统一输出。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.rows.append((name, passed, detail))

    @property
    def failed(self) -> int:
        return sum(1 for _, passed, _ in self.rows if not passed)

    def render(self) -> None:
        for name, passed, detail in self.rows:
            print(f"[{'通过' if passed else '失败'}] {name}：{detail}")


async def _count(session, table, condition) -> int:
    from sqlalchemy import func, select

    return int(await session.scalar(select(func.count()).select_from(table).where(condition)) or 0)


async def _scoped_business_tables(session, kb_id: str) -> list[tuple[str, Any, Any]]:
    """按导出脚本相同的作用域规则，列出该知识库涉及的业务表及其过滤条件。

    顺序与 ``BUSINESS_TABLES`` 一致（父表在前），子表按父表业务主键过滤。
    """
    from demo_data_spec import BUSINESS_KEY_COLUMN, BUSINESS_TABLES
    from sqlalchemy import select
    from yuxi.storage.postgres.models_knowledge import Base

    scoped: list[tuple[str, Any, Any]] = []
    parent_keys: dict[str, list[Any]] = {}
    for table_name, scope in BUSINESS_TABLES:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        if scope == "kb":
            condition = table.c.kb_id == kb_id
        elif isinstance(scope, tuple) and scope[0] == "kb":
            condition = table.c[scope[1]] == kb_id
        else:
            parent_table, parent_column = scope
            parent_values = parent_keys.get(parent_table) or []
            if not parent_values:
                continue
            condition = table.c[parent_column].in_(parent_values)
        key_column = BUSINESS_KEY_COLUMN.get(table_name)
        if key_column is not None:
            values = (await session.execute(select(table.c[key_column]).where(condition))).scalars().all()
            parent_keys[table_name] = list(values)
        scoped.append((table_name, table, condition))
    return scoped


async def _check_counts(session, kb_id: str, counts: dict[str, Any], report: _Report) -> None:
    """核对论文 / 文件 / chunk 的行数与导出快照一致。"""
    from yuxi.storage.postgres.models_knowledge import AcademicPaper, KnowledgeChunk, KnowledgeFile

    papers = await _count(session, AcademicPaper.__table__, AcademicPaper.__table__.c.kb_id == kb_id)
    expected_papers = int(counts.get("papers", 0))
    report.add(
        "论文数量",
        papers == expected_papers,
        f"期望 {expected_papers} 篇，实际 {papers} 篇",
    )

    files = await _count(session, KnowledgeFile.__table__, KnowledgeFile.__table__.c.kb_id == kb_id)
    expected_files = int(counts.get("papers_with_pdf", 0))
    report.add(
        "知识文件数量",
        files == expected_files,
        f"期望 {expected_files} 个，实际 {files} 个",
    )

    chunks = await _count(session, KnowledgeChunk.__table__, KnowledgeChunk.__table__.c.kb_id == kb_id)
    expected_chunks = int(counts.get("chunks", 0))
    report.add(
        "chunk 数量",
        chunks == expected_chunks,
        f"期望 {expected_chunks} 个，实际 {chunks} 个",
    )


async def _check_file_status(session, kb_id: str, report: _Report) -> None:
    """检查文件已索引，且登记的 chunk 数与实际 chunk 行数一致。"""
    from sqlalchemy import func, select
    from yuxi.storage.postgres.models_knowledge import KnowledgeChunk, KnowledgeFile

    files = KnowledgeFile.__table__
    chunks = KnowledgeChunk.__table__

    not_indexed = await _count(session, files, (files.c.kb_id == kb_id) & (files.c.status != "indexed"))
    report.add("文件索引状态", not_indexed == 0, f"未标记为已索引的文件 {not_indexed} 个")

    actual_by_file = (
        select(chunks.c.file_id, func.count().label("total"))
        .where(chunks.c.kb_id == kb_id)
        .group_by(chunks.c.file_id)
        .subquery()
    )
    actual_chunk_count = (
        select(actual_by_file.c.total).where(actual_by_file.c.file_id == files.c.file_id).scalar_subquery()
    )
    mismatched = await _count(
        session,
        files,
        (files.c.kb_id == kb_id) & (files.c.chunk_count.isnot(None)) & (files.c.chunk_count != actual_chunk_count),
    )
    report.add("文件登记 chunk 数", mismatched == 0, f"登记数与实际不一致的文件 {mismatched} 个")


async def _check_chunk_metadata(session, kb_id: str, bundle_dir: Path, report: _Report) -> None:
    """核对页码定位字段与论文快照是否按快照回填。"""
    from sqlalchemy import text

    package_chunks = _read_json(bundle_dir / "chunks.json")
    package_rows = [row for rows in package_chunks.values() for row in rows]
    package_total = len(package_rows)

    empty_content = await session.scalar(
        text("SELECT count(*) FROM knowledge_chunks WHERE kb_id = :kb AND (content IS NULL OR content = '')"),
        {"kb": kb_id},
    )
    report.add("chunk 正文完整", not empty_content, f"正文为空的 chunk {empty_content} 个")

    for key in PAGE_LOCATOR_KEYS:
        expected = sum(1 for row in package_rows if (row.get("chunk_metadata") or {}).get(key) is not None)
        actual = await session.scalar(
            text(f"SELECT count(*) FROM knowledge_chunks WHERE kb_id = :kb AND chunk_metadata ? '{key}'"),
            {"kb": kb_id},
        )
        report.add(
            f"页码定位字段 {key}",
            int(actual) == expected,
            f"期望 {expected} 个带该字段，实际 {int(actual)} 个",
        )

    with_paper = await session.scalar(
        text("SELECT count(*) FROM knowledge_chunks WHERE kb_id = :kb AND chunk_metadata ? 'paper'"),
        {"kb": kb_id},
    )
    report.add(
        "chunk 论文快照回填",
        int(with_paper) == package_total,
        f"期望 {package_total} 个带论文快照，实际 {int(with_paper)} 个",
    )


def _has_analysis_result(value: Any) -> bool:
    """判断分析结果是否真的有内容。

    JSON 列会把 Python ``None`` 存成 JSON ``null``，在 SQL 里仍然是 ``IS NOT NULL``；
    判空必须同时排除 ``None`` 与空容器，否则会把「没有分析结果」误判成「有」。
    """
    if value is None:
        return False
    if isinstance(value, (dict, list, str)) and len(value) == 0:
        return False
    return True


async def _check_chunk_analysis_results(session, kb_id: str, bundle_dir: Path, report: _Report) -> None:
    """核对 chunk 级分析结果是否随包还原。"""
    from demo_data_spec import CHUNK_ANALYSIS_FIELDS
    from sqlalchemy import text

    package_chunks = _read_json(bundle_dir / "chunks.json")
    package_rows = [row for rows in package_chunks.values() for row in rows]

    for field in CHUNK_ANALYSIS_FIELDS:
        expected = sum(1 for row in package_rows if _has_analysis_result(row.get(field)))
        actual = await session.scalar(
            text(
                "SELECT count(*) FROM knowledge_chunks "
                f"WHERE kb_id = :kb AND {field} IS NOT NULL "
                f"AND {field}::text NOT IN ('null', '{{}}', '[]')"
            ),
            {"kb": kb_id},
        )
        report.add(
            f"chunk 分析结果 {field}",
            int(actual) == expected,
            f"期望 {expected} 条带分析结果，实际 {int(actual)} 条",
        )


async def _check_business_rows(
    session, kb_id: str, counts: dict[str, Any], scoped: list[tuple[str, Any, Any]], report: _Report
) -> None:
    """逐表核对业务快照的行数。"""
    mismatches: list[str] = []
    for table_name, table, condition in scoped:
        expected = int(counts.get(table_name, 0))
        actual = await _count(session, table, condition)
        if actual != expected:
            mismatches.append(f"{table_name} 期望 {expected} 行实际 {actual} 行")
    report.add("业务表行数", not mismatches, "；".join(mismatches) if mismatches else "全部与导出快照一致")


async def _check_core_references(session, kb_id: str, report: _Report) -> None:
    """检查核心外键没有孤儿：chunk 与论文都必须挂在已登记的知识文件上。

    没有关联文件的论文（仅元数据的参考条目）不算孤儿。
    """
    from sqlalchemy import exists, literal, select
    from yuxi.storage.postgres.models_knowledge import AcademicPaper, KnowledgeChunk, KnowledgeFile

    files = KnowledgeFile.__table__
    for label, source in (
        ("chunk -> 知识文件", KnowledgeChunk.__table__),
        ("论文 -> 知识文件", AcademicPaper.__table__),
    ):
        reference = files.alias("f")
        exists_query = select(literal(1)).select_from(reference).where(reference.c.file_id == source.c.file_id)
        orphans = await _count(
            session,
            source,
            (source.c.kb_id == kb_id) & source.c.file_id.isnot(None) & ~exists(exists_query),
        )
        report.add(label, orphans == 0, f"孤儿记录 {orphans} 条")


async def _check_business_references(session, kb_id: str, scoped: list[tuple[str, Any, Any]], report: _Report) -> None:
    """按共享约定逐列检查业务表的跨表引用是否存在孤儿。"""
    from demo_data_spec import COLUMN_REMAP, reference_target
    from sqlalchemy import exists, literal, select
    from yuxi.storage.postgres.models_knowledge import Base

    problems: list[str] = []
    for table_name, table, condition in scoped:
        remap = COLUMN_REMAP.get(table_name, {})
        for column_name, target in remap.items():
            resolved = reference_target(target)
            if resolved is None:
                continue
            target_table_name, target_column = resolved
            target_table = Base.metadata.tables[target_table_name]
            reference = target_table.alias("ref")
            exists_query = (
                select(literal(1)).select_from(reference).where(reference.c[target_column] == table.c[column_name])
            )
            if "kb_id" in reference.c:
                exists_query = exists_query.where(reference.c.kb_id == kb_id)
            orphans = await _count(
                session,
                table,
                condition & table.c[column_name].isnot(None) & ~exists(exists_query),
            )
            if orphans:
                problems.append(f"{table_name}.{column_name} -> {target_table_name}.{target_column} 孤儿 {orphans} 条")
    report.add("业务表跨表引用", not problems, "；".join(problems) if problems else "未发现孤儿引用")


async def _check_retrieval(kb_id: str, query: str, top_k: int, search_mode: str, report: _Report) -> None:
    """跑一次真实检索，确认导入后的向量数据可用。"""
    from yuxi.knowledge.runtime import knowledge_base

    await knowledge_base.initialize()
    try:
        hits = await knowledge_base.aquery(query, kb_id, search_mode=search_mode, final_top_k=top_k)
    except Exception as exc:  # noqa: BLE001 - 检索失败要作为校验结果报出
        report.add("真实检索", False, f"检索失败：{type(exc).__name__}: {exc}")
        return

    if not hits:
        report.add("真实检索", False, f"检索「{query}」没有命中任何 chunk")
        return

    preview = []
    for hit in hits[:3]:
        metadata = hit.get("metadata") or {}
        paper = metadata.get("paper") or {}
        content = str(hit.get("content") or "")
        preview.append(
            f"{metadata.get('chunk_id')}（{paper.get('title') or '未知论文'}，"
            f"页码 {metadata.get('source_page_start')}，分数 {hit.get('score')}）：{content[:20]}…"
        )
    report.add(
        "真实检索",
        True,
        f"检索「{query}」命中 {len(hits)} 条（模式 {search_mode}）：" + "；".join(preview),
    )


async def verify(*, kb_id: str, bundle_id: str, data_dir: Path, query: str, top_k: int, search_mode: str) -> int:
    from yuxi.knowledge.runtime import knowledge_base
    from yuxi.storage.postgres.manager import pg_manager

    bundle_dir = data_dir / bundle_id
    manifest = _read_json(bundle_dir / "manifest.json")
    counts = manifest.get("counts", {})
    report = _Report()

    pg_manager.initialize()
    try:
        # 知识库元数据从 PostgreSQL 加载，必须在连接池关闭前初始化。
        await knowledge_base.initialize()
        async with pg_manager.get_async_session_context() as session:
            scoped = await _scoped_business_tables(session, kb_id)
            await _check_counts(session, kb_id, counts, report)
            await _check_file_status(session, kb_id, report)
            await _check_chunk_metadata(session, kb_id, bundle_dir, report)
            await _check_chunk_analysis_results(session, kb_id, bundle_dir, report)
            await _check_business_rows(session, kb_id, counts, scoped, report)
            await _check_core_references(session, kb_id, report)
            await _check_business_references(session, kb_id, scoped, report)
        await _check_retrieval(kb_id, query, top_k, search_mode, report)
    finally:
        await pg_manager.close()

    print(f"===== 校验演示数据包 {bundle_id} → 知识库 {kb_id} =====")
    report.render()
    failed = report.failed
    print(f"===== 结论：{len(report.rows) - failed}/{len(report.rows)} 项通过 =====")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校验演示数据包的导入结果（只读）")
    parser.add_argument("--kb-id", required=True, help="已导入的演示知识库 kb_id")
    parser.add_argument(
        "--bundle",
        default=None,
        help="待核对的数据包子目录名，默认与 --kb-id 相同（导入到新库时需指定源数据包）",
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="演示数据目录，默认仓库根目录 demo_data/")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="用于真实检索的检索式")
    parser.add_argument("--top-k", type=int, default=5, help="真实检索返回条数")
    parser.add_argument(
        "--search-mode",
        default="hybrid",
        choices=("vector", "keyword", "hybrid"),
        help="真实检索模式，默认 hybrid",
    )
    args = parser.parse_args()

    load_project_env()
    try:
        return asyncio.run(
            verify(
                kb_id=args.kb_id,
                bundle_id=args.bundle or args.kb_id,
                data_dir=Path(args.data_dir).resolve(),
                query=args.query,
                top_k=max(args.top_k, 1),
                search_mode=args.search_mode,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"演示数据校验失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
