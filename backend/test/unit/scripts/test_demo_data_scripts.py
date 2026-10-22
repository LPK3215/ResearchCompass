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
    CHUNK_ANALYSIS_FIELDS,
    COLUMN_REMAP,
    REDUNDANT_CHUNK_METADATA_KEYS,
    REMAP_KEY_PLAN_TARGETS,
    SELF_REFERENCE_PARENT_COLUMN,
    reference_target,
)
from yuxi.storage.postgres.models_knowledge import Base  # noqa: E402

from scripts.export_demo_data import _read_paper_bytes, _slugify  # noqa: E402
from scripts.import_demo_data import (  # noqa: E402
    _apply_column_remap,
    _build_chunk_records,
    _compare_expected,
    _discover_bundles,
    _order_parents_first,
)
from scripts.verify_demo_data import _has_analysis_result  # noqa: E402

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


def test_build_chunk_records_keeps_chunk_analysis_result() -> None:
    """chunk 级分析结论要随包往返，导入后不能丢。"""
    chunk_rows = [
        {
            "chunk_id": "old_1",
            "chunk_index": 0,
            "content": "正文",
            "chunk_metadata": {},
            "extraction_result": {"entities": [{"name": "RAG"}]},
        }
    ]

    records = _build_chunk_records("file_new", "paper.pdf", chunk_rows, {})

    assert records[0]["extraction_result"] == {"entities": [{"name": "RAG"}]}
    assert "extraction_result" in CHUNK_ANALYSIS_FIELDS


def test_read_paper_bytes_returns_none_for_missing_local_file(tmp_path: Path) -> None:
    """取不到原文的文件不能算可恢复，导出端据此排除它的 chunk。"""
    assert _read_paper_bytes(str(tmp_path / "missing.pdf"), dry_run=True) is None


def test_read_paper_bytes_reads_existing_local_file(tmp_path: Path) -> None:
    target = tmp_path / "paper.pdf"
    target.write_bytes(b"%PDF-1.4")

    assert _read_paper_bytes(str(target), dry_run=False) == b"%PDF-1.4"


def test_reference_target_resolves_every_remap_target() -> None:
    """每个重映射目标都要能翻译成物理引用，导入后的孤儿校验依赖它。"""
    unresolvable: set[str] = set()
    for table_name, remap in COLUMN_REMAP.items():
        for column_name, target in remap.items():
            resolved = reference_target(target)
            if resolved is None:
                unresolvable.add(target)
                continue
            target_table, target_column = resolved
            assert target_table in Base.metadata.tables, f"{target_table} 表不存在"
            assert target_column in _columns(target_table), f"{target_table} 缺少 {target_column} 列"
    # 只有邀请码哈希不对应任何外键列，其余目标都必须能落到真实表上。
    assert unresolvable == {"token"}


def test_token_remap_target_has_no_physical_reference() -> None:
    """邀请码哈希不对应外键列，孤儿校验要跳过它。"""
    assert reference_target("token") is None


def test_evaluation_experiments_task_reference_is_remapped() -> None:
    """消融实验挂在研究任务上，任务主键导入时会被改写，引用必须一起重映射。"""
    assert COLUMN_REMAP["evaluation_experiments"]["task_id"] == "task"
    assert reference_target("task") == ("research_project_tasks", "task_id")


def test_compare_expected_reports_mismatched_tables() -> None:
    expected = {
        "papers": 3,
        "papers_with_pdf": 3,
        "chunks": 10,
        "papers_without_pdf": 0,
        "research_evidence": 5,
        "research_projects": 2,
    }
    written = {"research_evidence": 5, "research_projects": 1}

    differences = _compare_expected(expected, written)

    assert differences == [("research_projects", 2, 1)]


def test_compare_expected_ignores_non_business_counts() -> None:
    """论文 / chunk 这类计数不走业务表比对。"""
    expected = {"papers": 1, "papers_with_pdf": 1, "chunks": 4, "papers_without_pdf": 0}

    assert _compare_expected(expected, {}) == []


def test_apply_column_remap_keeps_null_reference_untouched() -> None:
    """空的外键是合法状态，不能当成「未能解析」计数。"""
    values = {"run_id": "r1", "parent_run_id": None}

    resolved, unresolved, skip = _apply_column_remap(
        values,
        {"parent_run_id": "search_run"},
        lambda target, value: {"search_run": {"r0": "r0"}}[target].get(str(value)),
        {"parent_run_id": True},
    )

    assert resolved["parent_run_id"] is None
    assert unresolved == 0
    assert skip is False


def test_apply_column_remap_resolves_known_reference() -> None:
    values = {"run_id": "r1", "parent_run_id": "r0"}

    resolved, unresolved, skip = _apply_column_remap(
        values,
        {"parent_run_id": "search_run"},
        lambda target, value: {"search_run": {"r0": "new_r0"}}[target].get(str(value)),
        {"parent_run_id": True},
    )

    assert resolved["parent_run_id"] == "new_r0"
    assert unresolved == 0
    assert skip is False


def test_apply_column_remap_nulls_out_unresolvable_nullable_column() -> None:
    """指向范围外记录的引用置空并计数，不能留下悬挂外键。"""
    values = {"task_id": "missing"}

    resolved, unresolved, skip = _apply_column_remap(
        values,
        {"task_id": "task"},
        lambda target, value: None,
        {"task_id": True},
    )

    assert resolved["task_id"] is None
    assert unresolved == 1
    assert skip is False


def test_apply_column_remap_skips_row_with_unresolvable_required_column() -> None:
    """必填引用无法映射时整行跳过，不能写入半条记录。"""
    values = {"task_id": "missing"}

    resolved, unresolved, skip = _apply_column_remap(
        values,
        {"task_id": "task"},
        lambda target, value: None,
        {"task_id": False},
    )

    assert skip is True


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


def test_has_analysis_result_treats_json_null_and_empty_containers_as_absent() -> None:
    # JSON 列会把 Python None 存成 JSON null，判空必须一并排除，否则会把「没有分析结果」当成「有」。
    assert _has_analysis_result(None) is False
    assert _has_analysis_result({}) is False
    assert _has_analysis_result([]) is False
    assert _has_analysis_result("") is False


def test_has_analysis_result_accepts_real_payload() -> None:
    assert _has_analysis_result({"summary": "结论"}) is True
    assert _has_analysis_result([1, 2]) is True
    assert _has_analysis_result("已分析") is True
