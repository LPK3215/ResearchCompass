from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from yuxi.agents.toolkits.registry import get_extra_metadata
from yuxi.agents.toolkits.research import tools as research_tools
from yuxi.repositories.agent_repository import RESEARCH_COPILOT_TOOL_SLUGS
from yuxi.services import research_project_service, research_search_service, research_synthesis_service


def _runtime(*, project_id: str | None = "project-1") -> SimpleNamespace:
    return SimpleNamespace(
        context=SimpleNamespace(
            uid="user-1",
            research_context={"kb_id": "kb-1", "project_id": project_id},
        )
    )


@pytest.mark.asyncio
async def test_research_tool_runtime_is_hidden_and_injected_by_tool_node():
    assert set(research_tools.research_get_context.args_schema.model_fields) == {"dummy"}

    builder = StateGraph(dict, context_schema=object)
    builder.add_node(
        "tools",
        ToolNode([research_tools.research_get_context], handle_tool_errors=False),
    )
    builder.add_edge(START, "tools")
    graph = builder.compile()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "research_get_context",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    result = await graph.ainvoke(
        state,
        context=SimpleNamespace(
            uid="user-1",
            research_context={"kb_id": "kb-1", "project_id": "project-1"},
        ),
    )

    assert '"kb_id": "kb-1"' in result["messages"][-1].content


@pytest.mark.asyncio
async def test_bound_project_rejects_a_different_requested_project():
    with pytest.raises(ToolException, match="不能在当前项目会话中修改另一个研究项目"):
        await research_tools._project_id(_runtime(), "project-2", object())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bound_project_id", "requested_project_id"),
    [(None, "project-2"), ("project-1", None)],
)
async def test_project_scope_rejects_explicit_and_implicit_cross_kb_projects(
    monkeypatch,
    bound_project_id,
    requested_project_id,
):
    current_user = object()

    async def get_owned_project(project_id, user):
        assert project_id == (requested_project_id or bound_project_id)
        assert user is current_user
        return SimpleNamespace(kb_id="kb-2")

    monkeypatch.setattr(research_project_service, "get_owned_project", get_owned_project)

    with pytest.raises(ToolException, match="研究项目不属于当前知识库"):
        await research_tools._project_id(
            _runtime(project_id=bound_project_id),
            requested_project_id,
            current_user,
        )


@pytest.mark.asyncio
async def test_get_search_run_rejects_cross_kb_run(monkeypatch):
    current_user = object()

    async def get_current_user(_runtime):
        return current_user

    async def get_search_run(**kwargs):
        assert kwargs == {"run_id": "run-2", "current_user": current_user}
        return {"run_id": "run-2", "kb_id": "kb-2"}

    monkeypatch.setattr(research_tools, "_current_user", get_current_user)
    monkeypatch.setattr(research_search_service, "get_search_run", get_search_run)

    with pytest.raises(ToolException, match="检索运行不属于当前知识库"):
        await research_tools.research_get_search_run.coroutine(run_id="run-2", runtime=_runtime())


@pytest.mark.asyncio
async def test_search_papers_forwards_strict_citation_graph_mode(monkeypatch):
    current_user = object()
    captured = {}

    async def get_current_user(_runtime):
        return current_user

    async def search_papers(**kwargs):
        captured.update(kwargs)
        return {"run_id": "run-1"}

    monkeypatch.setattr(research_tools, "_current_user", get_current_user)
    monkeypatch.setattr(research_search_service, "search_papers", search_papers)

    result = await research_tools.research_search_papers.coroutine(
        query="citation graph",
        retrieval_mode="strict_hybrid_citation_graph",
        top_k=10,
        recall_top_k=50,
        year_from=None,
        year_to=None,
        runtime=_runtime(),
    )

    assert result == {"run_id": "run-1"}
    assert captured["retrieval_mode"] == "strict_hybrid_citation_graph"


def test_search_papers_schema_rejects_legacy_strict_graph_mode():
    with pytest.raises(ValidationError):
        research_tools.SearchPapersInput(
            query="citation graph",
            retrieval_mode="strict_hybrid_graph",
        )


@pytest.mark.asyncio
async def test_get_synthesis_rejects_cross_kb_run(monkeypatch):
    current_user = object()

    async def get_current_user(_runtime):
        return current_user

    async def get_synthesis(**kwargs):
        assert kwargs == {"run_id": "run-2", "current_user": current_user}
        return {"run_id": "run-2", "kb_id": "kb-2"}

    monkeypatch.setattr(research_tools, "_current_user", get_current_user)
    monkeypatch.setattr(research_synthesis_service, "get_research_synthesis", get_synthesis)

    with pytest.raises(ToolException, match="综述运行不属于当前知识库"):
        await research_tools.research_get_synthesis.coroutine(run_id="run-2", runtime=_runtime())


@pytest.mark.asyncio
async def test_regenerate_synthesis_forwards_run_and_current_user(monkeypatch):
    current_user = object()
    captured = {}

    async def get_current_user(runtime):
        assert runtime.context.uid == "user-1"
        return current_user

    async def get_synthesis(**kwargs):
        captured["get"] = kwargs
        return {"run_id": "source-run", "kb_id": "kb-1"}

    async def regenerate_synthesis(**kwargs):
        captured["regenerate"] = kwargs
        return {"run_id": "new-run"}

    monkeypatch.setattr(research_tools, "_current_user", get_current_user)
    monkeypatch.setattr(
        research_synthesis_service,
        "get_research_synthesis",
        get_synthesis,
    )
    monkeypatch.setattr(
        research_synthesis_service,
        "regenerate_research_synthesis",
        regenerate_synthesis,
    )

    result = await research_tools.research_regenerate_synthesis.coroutine(
        run_id="source-run",
        runtime=_runtime(),
    )

    assert result == {"run_id": "new-run"}
    expected_call = {"run_id": "source-run", "current_user": current_user}
    assert captured == {"get": expected_call, "regenerate": expected_call}


@pytest.mark.asyncio
async def test_regenerate_synthesis_rejects_cross_kb_before_creating_run(monkeypatch):
    current_user = object()
    regenerated = False

    async def get_current_user(_runtime):
        return current_user

    async def get_synthesis(**_kwargs):
        return {"run_id": "source-run", "kb_id": "kb-2"}

    async def regenerate_synthesis(**_kwargs):
        nonlocal regenerated
        regenerated = True
        return {"run_id": "new-run"}

    monkeypatch.setattr(research_tools, "_current_user", get_current_user)
    monkeypatch.setattr(research_synthesis_service, "get_research_synthesis", get_synthesis)
    monkeypatch.setattr(
        research_synthesis_service,
        "regenerate_research_synthesis",
        regenerate_synthesis,
    )

    with pytest.raises(ToolException, match="综述运行不属于当前知识库"):
        await research_tools.research_regenerate_synthesis.coroutine(
            run_id="source-run",
            runtime=_runtime(),
        )

    assert regenerated is False


@pytest.mark.asyncio
async def test_export_synthesis_rejects_cross_kb_before_exporting(monkeypatch):
    current_user = object()

    async def get_current_user(_runtime):
        return current_user

    async def get_synthesis(**_kwargs):
        return {"run_id": "source-run", "kb_id": "kb-2"}

    monkeypatch.setattr(research_tools, "_current_user", get_current_user)
    monkeypatch.setattr(
        research_synthesis_service,
        "get_research_synthesis",
        get_synthesis,
    )

    with pytest.raises(ToolException, match="综述运行不属于当前知识库"):
        await research_tools.research_export_synthesis.coroutine(
            run_id="source-run",
            format="markdown",
            runtime=_runtime(),
        )


@pytest.mark.asyncio
async def test_export_synthesis_writes_file_and_returns_artifact_path(monkeypatch, tmp_path):
    current_user = object()
    captured = {}

    async def get_current_user(_runtime):
        return current_user

    async def get_synthesis(**kwargs):
        captured["get"] = kwargs
        return {"run_id": "run-1", "kb_id": "kb-1"}

    async def export_synthesis(**kwargs):
        captured["export"] = kwargs
        return "research_synthesis_run1.md", b"# Markdown content", "text/markdown; charset=utf-8"

    monkeypatch.setattr(research_tools, "_current_user", get_current_user)
    monkeypatch.setattr(
        research_synthesis_service,
        "get_research_synthesis",
        get_synthesis,
    )
    monkeypatch.setattr(
        research_synthesis_service,
        "export_research_synthesis",
        export_synthesis,
    )

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            uid="user-1",
            thread_id="thread-1",
            file_thread_id=None,
            research_context={"kb_id": "kb-1", "project_id": "project-1"},
        )
    )

    from yuxi.agents.backends.sandbox import paths as sandbox_paths

    monkeypatch.setattr(sandbox_paths, "ensure_thread_dirs", lambda thread_id, uid: None)
    monkeypatch.setattr(sandbox_paths, "sandbox_outputs_dir", lambda thread_id: tmp_path)

    result = await research_tools.research_export_synthesis.coroutine(
        run_id="run-1",
        format="markdown",
        runtime=runtime,
    )

    assert result["run_id"] == "run-1"
    assert result["filename"] == "research_synthesis_run1.md"
    assert result["format"] == "markdown"
    assert result["artifact_path"].endswith("/research_synthesis_run1.md")
    assert (tmp_path / "research_synthesis_run1.md").read_bytes() == b"# Markdown content"
    assert captured["export"]["export_format"] == "markdown"


def test_research_copilot_registers_the_complete_tool_set():
    research_tool_names = [tool.name for tool in research_tools.RESEARCH_TOOLS]
    # research-copilot 启用全部 research 类工具，并额外启用 present_artifacts 用于展示导出文件
    assert set(research_tool_names).issubset(set(RESEARCH_COPILOT_TOOL_SLUGS))
    assert "present_artifacts" in RESEARCH_COPILOT_TOOL_SLUGS
    assert {get_extra_metadata(tool.name).category for tool in research_tools.RESEARCH_TOOLS} == {"research"}
