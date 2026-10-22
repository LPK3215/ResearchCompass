"""生成文档用的项目统计，避免在文档里手写版本号与数量。

真源全部来自仓库本身：

- 版本号：``backend/pyproject.toml`` 与 ``web/package.json``
- 最新标签：``git describe --tags --abbrev=0``
- 代码面：路由模块 / 服务模块 / 前端视图 / 科研工具 / API 路由装饰器
- 文档面：受版本控制的 Markdown 数量与分组

产物是两个 Markdown 表格，默认写入 ``docs/project/项目统计-自动生成.md``。

用法::

    python scripts/doc_stats.py            # 写文件
    python scripts/doc_stats.py --check    # 只校验现有文件是否过期，过期返回 1
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
WEB_DIR = REPO_ROOT / "web"
OUTPUT_FILE = REPO_ROOT / "docs" / "project" / "项目统计-自动生成.md"

ROUTE_DECORATOR = re.compile(r"^@\w+\.(get|post|put|patch|delete)\(", re.MULTILINE)
TOOL_DECORATOR = re.compile(r"^@tool\(", re.MULTILINE)

# 文档分组的展示名 -> 仓库内相对目录
DOC_GROUPS = {
    "仓库根目录": "",
    "使用文档 docs/intro": "docs/intro",
    "高级配置 docs/advanced": "docs/advanced",
    "智能体开发 docs/agents": "docs/agents",
    "开发指南 docs/develop-guides": "docs/develop-guides",
    "项目档案 docs/project": "docs/project",
}


def read_version(path: Path) -> str:
    if path.suffix == ".toml":
        return str(tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"])
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


def latest_tag() -> str:
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip() if result.returncode == 0 else "（无标签）"


def count_pattern(paths: list[Path], pattern: re.Pattern[str]) -> int:
    return sum(len(pattern.findall(path.read_text(encoding="utf-8"))) for path in paths)


def py_modules(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.py") if path.name != "__init__.py")


def tracked_markdown() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def render() -> str:
    backend_version = read_version(BACKEND_DIR / "pyproject.toml")
    web_version = read_version(WEB_DIR / "package.json")
    routers = py_modules(BACKEND_DIR / "server" / "routers")
    services = py_modules(BACKEND_DIR / "package" / "yuxi" / "services")
    views = sorted((WEB_DIR / "src" / "views").glob("*.vue"))
    tools_file = BACKEND_DIR / "package" / "yuxi" / "agents" / "toolkits" / "research" / "tools.py"

    code_rows = [
        ("后端版本（backend/pyproject.toml）", backend_version),
        ("前端版本（web/package.json）", web_version),
        ("最新 git 标签", latest_tag()),
        ("路由模块 backend/server/routers", len(routers)),
        ("HTTP 路由装饰器", count_pattern(routers, ROUTE_DECORATOR)),
        ("服务模块 backend/package/yuxi/services", len(services)),
        ("科研工具 @tool", count_pattern([tools_file], TOOL_DECORATOR) if tools_file.exists() else 0),
        ("前端视图 web/src/views", len(views)),
    ]

    doc_counts = []
    markdown_files = tracked_markdown()
    for label, relative in DOC_GROUPS.items():
        if relative:
            matched = [path for path in markdown_files if path.startswith(f"{relative}/")]
        else:
            matched = [path for path in markdown_files if "/" not in path]
        doc_counts.append((label, len(matched)))
    doc_counts.append(("受版本控制的 Markdown 合计", len(markdown_files)))

    lines = [
        "# 项目统计（自动生成）",
        "",
        "> 本文件由 `backend/scripts/doc_stats.py` 生成，**不要手改**。",
        "> 重新生成：`cd backend && python scripts/doc_stats.py`；",
        "> 仅校验是否过期：`cd backend && python scripts/doc_stats.py --check`。",
        "",
        "## 版本与代码面",
        "",
        "| 项目 | 数值 |",
        "| --- | --- |",
        *(f"| {label} | {value} |" for label, value in code_rows),
        "",
        "## 文档面",
        "",
        "| 分组 | 文件数 |",
        "| --- | --- |",
        *(f"| {label} | {value} |" for label, value in doc_counts),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成文档用的项目统计")
    parser.add_argument("--check", action="store_true", help="只校验现有文件是否过期")
    args = parser.parse_args()

    content = render()
    if args.check:
        if not OUTPUT_FILE.exists() or OUTPUT_FILE.read_text(encoding="utf-8") != content:
            print(f"{OUTPUT_FILE.relative_to(REPO_ROOT)} 已过期，请重新生成", file=sys.stderr)
            return 1
        print("统计数据是最新的")
        return 0

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(content, encoding="utf-8")
    print(f"已写入 {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
