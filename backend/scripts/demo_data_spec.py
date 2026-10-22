"""演示数据导出 / 导入脚本共享的表结构约定。

``scripts/export_demo_data.py`` 与 ``scripts/import_demo_data.py`` 必须对
"哪些业务表参与演示数据""导入时哪些列需要按目标环境重写"保持一致，
因此把这份约定集中在这里，避免两份脚本各写一份而逐渐跑偏。
"""

from __future__ import annotations

from typing import Any

# 演示业务数据涉及的表：``(表名, 作用域)``，**顺序即导入顺序**，必须满足外键依赖。
#
# 作用域三选一：
#   ``"kb"``               表自带 ``kb_id`` 列，按该列过滤。
#   ``("kb", 列名)``        用指定列存 ``kb_id``（如 ``evaluation_experiments.source_kb_id``）。
#   ``(父表, 关联列)``      按父表导出的业务主键集合过滤。
BUSINESS_TABLES: tuple[tuple[str, Any], ...] = (
    ("research_paper_notes", "kb"),
    ("academic_paper_screenings", "kb"),
    ("academic_paper_tags", "kb"),
    ("research_search_runs", "kb"),
    ("research_synthesis_runs", "kb"),
    ("academic_paper_analysis_runs", "kb"),
    ("evaluation_datasets", "kb"),
    ("evaluation_dataset_items", "kb"),
    ("evaluation_runs", "kb"),
    ("evaluation_run_items", ("evaluation_runs", "run_id")),
    ("evaluation_experiments", ("kb", "source_kb_id")),
    ("evaluation_experiment_variants", "kb"),
    ("research_user_studies", "kb"),
    ("research_user_study_invites", ("research_user_studies", "study_id")),
    ("research_user_study_responses", ("research_user_studies", "study_id")),
    ("research_projects", "kb"),
    ("research_project_milestones", ("research_projects", "project_id")),
    ("research_project_tasks", ("research_projects", "project_id")),
    ("research_project_risks", ("research_projects", "project_id")),
    ("research_project_assets", ("research_projects", "project_id")),
    ("research_evidence", "kb"),
)

# 每张业务表对外可见的业务主键列。导入时若与目标环境已有记录冲突则重新生成。
BUSINESS_KEY_COLUMN: dict[str, str] = {
    "research_paper_notes": "note_id",
    "research_search_runs": "run_id",
    "research_synthesis_runs": "run_id",
    "academic_paper_analysis_runs": "run_id",
    "evaluation_datasets": "dataset_id",
    "evaluation_dataset_items": "item_id",
    "evaluation_runs": "run_id",
    "evaluation_experiments": "experiment_id",
    "evaluation_experiment_variants": "variant_id",
    "research_user_studies": "study_id",
    "research_user_study_invites": "invite_id",
    "research_user_study_responses": "response_id",
    "research_projects": "project_id",
    "research_project_tasks": "task_id",
    "research_project_milestones": "milestone_id",
    "research_project_risks": "risk_id",
    "research_project_assets": "asset_id",
    "research_evidence": "evidence_id",
}

# 导入时需要按目标环境重写的列：``表名 -> {列名: 映射目标}``。
#
# 映射目标含义：
#   kb          目标知识库 kb_id
#   owner       导入者 uid
#   paper       学术论文业务主键（字符串 paper_id）
#   paper_row   academic_papers.id（整型自增主键）
#   chunk       chunk_id
#   token       重新生成的邀请码哈希（原邀请明文不可恢复，只能重新生成）
#   其余目标见 ``REMAP_KEY_PLAN_TARGETS``：从业务主键重映射表里取新值。
COLUMN_REMAP: dict[str, dict[str, str]] = {
    "research_paper_notes": {"kb_id": "kb", "paper_id": "paper", "chunk_id": "chunk", "uid": "owner"},
    "academic_paper_screenings": {"kb_id": "kb", "paper_id": "paper", "uid": "owner"},
    "academic_paper_tags": {"kb_id": "kb", "paper_id": "paper", "uid": "owner"},
    "research_search_runs": {"kb_id": "kb", "uid": "owner", "parent_run_id": "search_run"},
    "research_synthesis_runs": {
        "kb_id": "kb",
        "uid": "owner",
        "parent_run_id": "synth_run",
        "task_id": "task",
    },
    "academic_paper_analysis_runs": {"kb_id": "kb", "academic_paper_id": "paper_row", "uid": "owner"},
    "evaluation_datasets": {"kb_id": "kb", "created_by": "owner"},
    "evaluation_dataset_items": {"kb_id": "kb", "dataset_id": "dataset"},
    "evaluation_runs": {
        "kb_id": "kb",
        "created_by": "owner",
        "dataset_id": "dataset",
        "experiment_id": "experiment",
        "variant_id": "variant",
    },
    "evaluation_run_items": {"run_id": "eval_run", "dataset_item_id": "dataset_item"},
    "evaluation_experiments": {
        "source_kb_id": "kb",
        "dataset_id": "dataset",
        "baseline_variant_id": "variant",
        # 消融实验可能挂在研究任务上，任务主键导入时会改写，必须一起重映射。
        "task_id": "task",
        "created_by": "owner",
    },
    "evaluation_experiment_variants": {
        "kb_id": "kb",
        "experiment_id": "experiment",
        "dataset_id": "dataset",
        "run_id": "eval_run",
    },
    "research_user_studies": {"kb_id": "kb", "created_by": "owner"},
    "research_user_study_invites": {"study_id": "study", "token_hash": "token"},
    "research_user_study_responses": {"study_id": "study", "invite_id": "invite"},
    "research_projects": {"kb_id": "kb", "uid": "owner"},
    "research_project_tasks": {"project_id": "project", "milestone_id": "milestone"},
    "research_project_milestones": {"project_id": "project"},
    "research_project_risks": {"project_id": "project", "owner_uid": "owner", "created_by": "owner"},
    "research_project_assets": {"project_id": "project"},
    "research_evidence": {
        "kb_id": "kb",
        "source_chunk_id": "chunk",
        "created_by": "owner",
        "verified_by": "owner",
        "archived_by": "owner",
    },
}

# 映射目标 -> 业务主键重映射表：``目标 -> 表名``。
# 导入时按 ``key_plan[表名][旧业务主键]`` 取目标环境里的新值。
REMAP_KEY_PLAN_TARGETS: dict[str, str] = {
    "search_run": "research_search_runs",
    "synth_run": "research_synthesis_runs",
    "eval_run": "evaluation_runs",
    "dataset": "evaluation_datasets",
    "dataset_item": "evaluation_dataset_items",
    "experiment": "evaluation_experiments",
    "variant": "evaluation_experiment_variants",
    "project": "research_projects",
    "task": "research_project_tasks",
    "milestone": "research_project_milestones",
    "study": "research_user_studies",
    "invite": "research_user_study_invites",
}

# chunk 快照里随包导出的分析结果字段：导入时原样写回，避免 chunk 级分析结论丢失。
CHUNK_ANALYSIS_FIELDS: tuple[str, ...] = ("extraction_result",)

# 重映射目标 -> 物理引用目标 ``(表名, 列名)``。
# 导入后做跨表引用（孤儿）校验时按这张表逐列核对，``token`` 目标没有对应外键列。
REFERENCE_TARGET: dict[str, tuple[str, str]] = {
    "kb": ("knowledge_bases", "kb_id"),
    "owner": ("users", "uid"),
    "paper": ("academic_papers", "paper_id"),
    "paper_row": ("academic_papers", "id"),
    "chunk": ("knowledge_chunks", "chunk_id"),
}


def reference_target(target: str) -> tuple[str, str] | None:
    """把重映射目标翻译成物理引用目标 ``(表名, 列名)``。

    ``token``（研究邀请码哈希）不对应任何外键列，返回 ``None``。
    """
    if target in REFERENCE_TARGET:
        return REFERENCE_TARGET[target]
    table_name = REMAP_KEY_PLAN_TARGETS.get(target)
    if table_name is None:
        return None
    key_column = BUSINESS_KEY_COLUMN.get(table_name)
    return (table_name, key_column) if key_column else None


# 自引用外键：``表名 -> 自引用列``。导入时父行必须先于子行写入，否则外键校验失败。
SELF_REFERENCE_PARENT_COLUMN: dict[str, str] = {
    "research_search_runs": "parent_run_id",
    "research_synthesis_runs": "parent_run_id",
}

# chunk_metadata 里逐 chunk 重复的论文快照，导出时去掉，导入时按 manifest 回填。
REDUNDANT_CHUNK_METADATA_KEYS = ("paper",)

# chunk_metadata["paper"] 快照保留的字段，与解析管线写入的结构保持一致。
PAPER_METADATA_KEYS: tuple[str, ...] = (
    "paper_id",
    "title",
    "abstract",
    "authors",
    "venue",
    "doi",
    "keywords",
    "language",
    "external_ids",
    "citation_count",
    "metadata_source",
    "metadata_status",
    "metadata_revision",
    "indexed_revision",
    "publication_year",
)
