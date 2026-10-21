"""研究工具调用与 service 函数签名的静态一致性守卫。

工具层用关键字参数调用 service，关键字拼错只会在真实对话中变成 ToolException。
这里解析 tools.py 中所有 ``from yuxi... import ...`` 后的调用，核对关键字与必填参数
和实际函数签名是否一致，把这类问题拦在提交前。
"""

import ast
import importlib
import inspect
from pathlib import Path

TOOLS_PATH = (
    Path(__file__).resolve().parents[3] / "package" / "yuxi" / "agents" / "toolkits" / "research" / "tools.py"
)


def _service_calls(tree: ast.AST) -> list[tuple[str, str, ast.Call]]:
    imports = {
        alias.asname or alias.name: node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("yuxi.")
        for alias in node.names
    }
    return [
        (imports[node.func.id], node.func.id, node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in imports
    ]


def _signature_problems() -> list[str]:
    tree = ast.parse(TOOLS_PATH.read_text(encoding="utf-8"))
    problems: list[str] = []
    for module_path, function_name, call in _service_calls(tree):
        parameters = inspect.signature(getattr(importlib.import_module(module_path), function_name)).parameters
        keyword_names = {keyword.arg for keyword in call.keywords if keyword.arg}
        label = f"{module_path}.{function_name}"

        accepts_any_keyword = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        unknown = sorted(name for name in keyword_names if name not in parameters)
        if unknown and not accepts_any_keyword:
            problems.append(f"{label}: 未知关键字参数 {unknown}")

        missing = [
            name
            for index, (name, parameter) in enumerate(parameters.items())
            if index >= len(call.args)
            and parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            and name not in keyword_names
            and parameter.default is inspect.Parameter.empty
        ]
        if missing:
            problems.append(f"{label}: 缺少必填参数 {missing}")
    return problems


def test_research_tool_calls_match_service_signatures():
    assert _signature_problems() == []
