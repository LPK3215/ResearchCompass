"""内置研究项目模板及模板实例化逻辑。"""

from __future__ import annotations

from typing import Any


PROJECT_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "template_id": "systematic-review",
        "name": "系统综述",
        "description": "按检索、筛选、证据提取和写作阶段组织系统综述。",
        "milestones": (
            {
                "title": "检索与筛选",
                "description": "完成检索策略和纳入排除标准。",
                "tasks": ("制定检索策略", "筛选候选论文"),
            },
            {"title": "证据提取", "description": "验证并结构化研究证据。", "tasks": ("提取关键证据", "复核证据质量")},
            {
                "title": "综合与写作",
                "description": "完成证据综合和综述交付。",
                "tasks": ("生成证据综述", "审阅并导出最终稿"),
            },
        ),
    },
    {
        "template_id": "empirical-study",
        "name": "实证研究",
        "description": "按问题、方法、分析和成果阶段推进实证研究。",
        "milestones": (
            {
                "title": "研究设计",
                "description": "明确问题、假设与研究方法。",
                "tasks": ("定义研究问题", "确定研究方法"),
            },
            {"title": "数据与分析", "description": "完成数据准备和分析。", "tasks": ("准备研究数据", "执行并记录分析")},
            {
                "title": "结论与发布",
                "description": "形成结论并准备发布材料。",
                "tasks": ("整理研究结论", "准备论文初稿"),
            },
        ),
    },
)


def list_project_templates() -> dict[str, Any]:
    """返回不含内部任务细节的模板目录。"""
    return {
        "items": [
            {
                "template_id": template["template_id"],
                "name": template["name"],
                "description": template["description"],
                "milestone_count": len(template["milestones"]),
                "task_count": sum(len(item["tasks"]) for item in template["milestones"]),
            }
            for template in PROJECT_TEMPLATES
        ]
    }


def get_project_template(template_id: str) -> dict[str, Any] | None:
    return next((item for item in PROJECT_TEMPLATES if item["template_id"] == template_id), None)
