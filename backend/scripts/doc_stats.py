"""生成文档用的项目统计，避免在文档里手写版本号与数量。

真源全部来自仓库本身：

- 版本号：``backend/pyproject.toml`` 与 ``web/package.json``
- 最新标签：``git describe --tags --abbrev=0``
- 代码面：路由模块 / 服务模块 / 前端视图 / 科研工具 / API 路由装饰器
- 文档面：项目内 Markdown 数量与分组

产物是两个 Markdown 表格与一个站点版本 JSON，默认写入
``docs/project/项目统计-自动生成.md`` 和 ``docs/.vitepress/theme/generated-stats.json``。

用法::

    python scripts/doc_stats.py            # 写文件
    python scripts/doc_stats.py --check    # 只校验现有文件是否过期，过期返回 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
WEB_DIR = REPO_ROOT / "web"
OUTPUT_FILE = REPO_ROOT / "docs" / "project" / "项目统计-自动生成.md"
SITE_STATS_FILE = REPO_ROOT / "docs" / ".vitepress" / "theme" / "generated-stats.json"
BUILTIN_PROVIDERS_FILE = BACKEND_DIR / "package" / "yuxi" / "models" / "providers" / "builtin.py"

ROUTE_DECORATOR = re.compile(r"^@\w+\.(get|post|put|patch|delete)\(", re.MULTILINE)
TOOL_DECORATOR = re.compile(r"^@tool\(", re.MULTILINE)

# 文档分组的展示名 -> 仓库内相对目录
DOC_GROUPS = {
    "仓库根目录": "",
    "GitHub 模板 .github": ".github",
    "后端 backend": "backend",
    "示例数据 demo_data": "demo_data",
    "使用文档 docs/intro": "docs/intro",
    "高级配置 docs/advanced": "docs/advanced",
    "智能体开发 docs/agents": "docs/agents",
    "开发指南 docs/develop-guides": "docs/develop-guides",
    "项目档案 docs/project": "docs/project",
    "研究笔记 docs/vibe": "docs/vibe",
}

EXCLUDED_PARTS = {
    ".archify",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vitepress",
    "__pycache__",
    "backups",
    "dist",
    "docker",
    "home",
    "node_modules",
    "saves",
    "test-results",
    "tmp",
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


def project_markdown() -> list[str]:
    """Return project Markdown from the working tree, excluding runtime and generated trees."""
    files: list[str] = []
    for root, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDED_PARTS]
        for filename in filenames:
            if not filename.endswith(".md"):
                continue
            files.append((Path(root) / filename).relative_to(REPO_ROOT).as_posix())
    return sorted(files)


def builtin_provider_count() -> int:
    if not BUILTIN_PROVIDERS_FILE.exists():
        return 0
    return sum(
        1
        for line in BUILTIN_PROVIDERS_FILE.read_text(encoding="utf-8").splitlines()
        if '"provider_id"' in line and not line.lstrip().startswith("#")
    )


def render_site_stats() -> str:
    """Version and provider facts consumed by the VitePress home page."""
    payload = {
        "release_tag": latest_tag(),
        "backend_version": read_version(BACKEND_DIR / "pyproject.toml"),
        "web_version": read_version(WEB_DIR / "package.json"),
        "provider_count": builtin_provider_count(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


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
    markdown_files = project_markdown()
    for label, relative in DOC_GROUPS.items():
        if relative:
            matched = [path for path in markdown_files if path.startswith(f"{relative}/")]
        else:
            matched = [path for path in markdown_files if "/" not in path]
        doc_counts.append((label, len(matched)))
    doc_counts.append(("文档首页 docs/index.md", 1 if "docs/index.md" in markdown_files else 0))
    grouped = sum(count for _, count in doc_counts)
    doc_counts.append(("其他项目文档", max(len(markdown_files) - grouped, 0)))
    doc_counts.append(("项目 Markdown 合计", len(markdown_files)))

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
    site_content = render_site_stats()
    if args.check:
        stale = []
        if not OUTPUT_FILE.exists() or OUTPUT_FILE.read_text(encoding="utf-8") != content:
            stale.append(OUTPUT_FILE)
        if not SITE_STATS_FILE.exists() or SITE_STATS_FILE.read_text(encoding="utf-8") != site_content:
            stale.append(SITE_STATS_FILE)
        if stale:
            for path in stale:
                print(f"{path.relative_to(REPO_ROOT)} 已过期，请重新生成", file=sys.stderr)
            return 1
        print("统计数据是最新的")
        return 0

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(content, encoding="utf-8")
    SITE_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SITE_STATS_FILE.write_text(site_content, encoding="utf-8")
    print(f"已写入 {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    print(f"已写入 {SITE_STATS_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
