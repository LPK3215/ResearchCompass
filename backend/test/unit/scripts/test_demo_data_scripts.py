"""演示数据包（导出 / 导入）脚本的表结构约定与纯逻辑守卫。

这些用例不依赖运行中的服务：只校验共享约定 ``scripts/demo_data_spec.py`` 与
数据库模型元数据一致，以及导入脚本里两个纯函数的容错语义。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
for import_path in (BACKEND_ROOT, BACKEND_ROOT / "package", BACKEND_ROOT / "scripts"):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from demo_data_spec import (  # noqa: E402
    BUSINESS_KEY_COLUMN,
    BUSINESS_TABLES,
    COLUMN_REMAP,
    REDUNDANT_CHUNK_METADATA_KEYS,
    REMAP_KEY_PLAN_TARGETS,
    SELF_REFERENCE_PARENT_COLUMN,
)
from yuxi.storage.postgres.models_knowledge import Base  # noqa: E402

from scripts.export_demo_data import _slugify  # noqa: E402
from scripts.import_demo_data import _build_chunk_records, _discover_bundles, _order_parents_first  # noqa: E402

SUPPORTED_REMAP_TARGETS = {"kb", "owner", "paper", "paper_row", "chunk", "token"} | set(REMAP_KEY_PLAN_TARGETS)


def _columns(table_name: str) -> set[str]:
    return {column.name for column in Base.metadata.tables[table_name].columns}


def test_business_tables_and_scope_columns_exist() -> None:
    for table_name, scope in BUSINESS_TABLES:
        assert table_name in Base.metadata.tables, f"业务表 {table_name} 不存在"
        if scope == "kb":
            assert "kb_id" in _columns(table_name), f"{table_name} 缺少 kb_id 列"
        elif isinstance(scope, tuple) and scope[0] == "kb":
            assert scope[1] in _columns(table_name), f"{table_name} 缺少作用域列 {scope[1]}"


def test_business_key_columns_exist() -> None:
    for table_name, key_column in BUSINESS_KEY_COLUMN.items():
        assert table_name in Base.metadata.tables, f"业务表 {table_name} 不存在"
        assert key_column in _columns(table_name), f"{table_name} 缺少业务主键列 {key_column}"


def test_column_remap_columns_exist() -> None:
    for table_name, remap in COLUMN_REMAP.items():
        columns = _columns(table_name)
        for column_name in remap:
            assert column_name in columns, f"{table_name} 缺少待重映射列 {column_name}"


def test_self_reference_columns_exist() -> None:
    for table_name, parent_column in SELF_REFERENCE_PARENT_COLUMN.items():
        assert parent_column in _columns(table_name), f"{table_name} 缺少自引用列 {parent_column}"


def test_remap_targets_are_all_supported() -> None:
    used_targets = {target for remap in COLUMN_REMAP.values() for target in remap.values()}
    assert used_targets <= SUPPORTED_REMAP_TARGETS, f"存在未实现的映射目标：{used_targets - SUPPORTED_REMAP_TARGETS}"


def test_table_order_places_parents_before_children() -> None:
    """导入按 BUSINESS_TABLES 顺序写入，父表必须排在引用它的子表之前。"""
    order = {table_name: index for index, (table_name, _) in enumerate(BUSINESS_TABLES)}
    for table_name, scope in BUSINESS_TABLES:
        if not isinstance(scope, tuple) or scope[0] == "kb":
            continue
        parent_table = scope[0]
        assert order[parent_table] < order[table_name], f"{table_name} 必须排在父表 {parent_table} 之后"


def test_order_parents_first_writes_parent_before_child() -> None:
    rows = [
        {"run_id": "child", "parent_run_id": "parent"},
        {"run_id": "parent", "parent_run_id": None},
    ]

    ordered = _order_parents_first(rows, "run_id", "parent_run_id")

    assert [row["run_id"] for row in ordered] == ["parent", "child"]


def test_order_parents_first_handles_broken_parent_reference() -> None:
    """父行缺失时不应递归爆栈，孤行照常写入。"""
    rows = [{"run_id": "orphan", "parent_run_id": "missing"}]

    ordered = _order_parents_first(rows, "run_id", "parent_run_id")

    assert [row["run_id"] for row in ordered] == ["orphan"]


def test_build_chunk_records_scopes_ids_to_file_and_restores_paper_snapshot() -> None:
    chunk_rows = [
        {"chunk_id": "old_1", "chunk_index": 0, "content": "第一段", "chunk_metadata": {"source_page_start": 3}},
        {"chunk_id": "old_2", "chunk_index": 1, "content": "第二段", "chunk_metadata": {}},
    ]

    records = _build_chunk_records("file_new", "paper.pdf", chunk_rows, {"paper_id": "p1", "title": "标题"})

    assert [record["id"] for record in records] == ["file_new_chunk_0", "file_new_chunk_1"]
    assert [record["chunk_id"] for record in records] == ["file_new_chunk_0", "file_new_chunk_1"]
    assert records[0]["file_id"] == "file_new"
    assert records[0]["filename"] == "paper.pdf"
    assert records[0]["content"] == "第一段"
    assert records[0]["chunk_metadata"]["source_page_start"] == 3
    assert records[0]["chunk_metadata"]["paper"] == {"paper_id": "p1", "title": "标题"}
    # “paper” 快照导出时被当作冗余字段删掉，导入时按 manifest 回填。
    assert "paper" in REDUNDANT_CHUNK_METADATA_KEYS


def test_slugify_keeps_filename_safe() -> None:
    assert _slugify("Attention Is All You Need!") == "attention-is-all-you-need"
    assert _slugify("") == "paper"
    assert "/" not in _slugify("a/b\\c")


def test_discover_bundles_finds_each_kb_directory(tmp_path: Path) -> None:
    (tmp_path / "kb_first").mkdir()
    (tmp_path / "kb_first" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "kb_second").mkdir()
    (tmp_path / "kb_second" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "not_a_bundle").mkdir()

    bundles = _discover_bundles(tmp_path)

    assert [bundle.name for bundle in bundles] == ["kb_first", "kb_second"]


def test_discover_bundles_accepts_single_bundle_directory(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")

    assert _discover_bundles(tmp_path) == [tmp_path]
