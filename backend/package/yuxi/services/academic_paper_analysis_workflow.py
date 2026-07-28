"""ResearchCompass 论文分析四阶段 LangGraph 工作流。

本模块是本仓库作者在 Yuxi 提供的 LangGraph 运行态之上编排的论文分析流程图：把
论文分析拆为 structure / innovations / methodology / gaps 四个阶段节点，按线性
顺序串联，每个节点只负责调用一次阶段执行器并把结果合并进共享状态。LangGraph 的
StateGraph、节点编排与运行时由 Yuxi（LangGraph）提供；本模块仅定义四阶段图的
拓扑与状态合并语义。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph


class AnalysisState(TypedDict, total=False):
    paper_context: str
    stage_results: dict[str, Any]


StageRunner = Callable[[str, str], Awaitable[dict[str, Any]]]


def build_analysis_workflow(stage_runner: StageRunner):
    """构建四阶段论文分析图，每个节点只负责一个结构化阶段。"""

    async def run_stage(state: AnalysisState, stage: str) -> AnalysisState:
        previous = state.get("stage_results") or {}
        context = state["paper_context"]
        if stage != "structure":
            context += "\n\n前序结构化分析:\n" + json.dumps(previous, ensure_ascii=False)
        result = await stage_runner(stage, context)
        return {"stage_results": {**previous, stage: result}}

    async def structure(state: AnalysisState) -> AnalysisState:
        return await run_stage(state, "structure")

    async def innovations(state: AnalysisState) -> AnalysisState:
        return await run_stage(state, "innovations")

    async def methodology(state: AnalysisState) -> AnalysisState:
        return await run_stage(state, "methodology")

    async def gaps(state: AnalysisState) -> AnalysisState:
        return await run_stage(state, "gaps")

    workflow = StateGraph(AnalysisState)
    workflow.add_node("structure", structure)
    workflow.add_node("innovations", innovations)
    workflow.add_node("methodology", methodology)
    workflow.add_node("gaps", gaps)
    workflow.add_edge(START, "structure")
    workflow.add_edge("structure", "innovations")
    workflow.add_edge("innovations", "methodology")
    workflow.add_edge("methodology", "gaps")
    workflow.add_edge("gaps", END)
    return workflow.compile()


__all__ = ["AnalysisState", "build_analysis_workflow"]
