"""ResearchCompass 冒烟验收：每个能力族至少一条真实请求。

前置：后端运行在 SMOKE_BASE_URL（默认 http://127.0.0.1:5050），基础设施与本地向量服务可用。
结果写入 test-results/smoke-acceptance-<时间戳>.{json,md}，任一必需项失败时退出码为 1。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:5050").rstrip("/")
REPORT_DIR = ROOT / "test-results"
RESEARCH_COPILOT_AGENT_SLUG = "research-copilot"
SMOKE_REFERENCE = """
@article{smoke_acceptance_ref,
  title = {Smoke Acceptance Reference},
  author = {Smoke, Tester},
  year = {2026},
  doi = {10.9999/smoke.acceptance.ref}
}
"""


def _load_admin_credentials() -> tuple[str, str]:
    uid = os.getenv("ADMIN_UID", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if uid and password:
        return uid, password
    env_path = ROOT / "saves/local/dev_admin.env"
    if not env_path.exists():
        raise RuntimeError("缺少 ADMIN_UID / ADMIN_PASSWORD，且找不到 saves/local/dev_admin.env")
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values["ADMIN_UID"], values["ADMIN_PASSWORD"]


def _summarize(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = response.json()
        except ValueError:
            return f"json_invalid bytes={len(response.content)}"
        if isinstance(payload, list):
            return f"list[{len(payload)}]"
        if isinstance(payload, dict):
            parts = []
            for key in ("total", "count", "status", "message", "imported_count", "skipped_count", "failed_count"):
                if key in payload:
                    parts.append(f"{key}={payload[key]}")
            for key in ("items", "nodes", "edges", "databases", "opportunities", "groups", "papers"):
                value = payload.get(key)
                if isinstance(value, list):
                    parts.append(f"{key}={len(value)}")
            detail = payload.get("detail")
            if detail is not None:
                if isinstance(detail, dict):
                    parts.append(f"error={detail.get('error')} message={detail.get('message')}")
                else:
                    parts.append(f"detail={detail}")
            return " ".join(parts) or f"keys={','.join(list(payload)[:6])}"
        return type(payload).__name__
    return f"bytes={len(response.content)}"


class SmokeRun:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.checks: list[dict[str, Any]] = []

    def record(self, family: str, name: str, ok: bool, status: int | None, detail: str, duration_ms: int = 0) -> None:
        self.checks.append(
            {
                "family": family,
                "name": name,
                "ok": ok,
                "status": status,
                "detail": detail,
                "duration_ms": duration_ms,
            }
        )
        print(("PASS" if ok else "FAIL"), family, name, status, detail)

    async def check(
        self,
        family: str,
        name: str,
        method: str,
        path: str,
        *,
        expect: int = 200,
        validate: Callable[[httpx.Response], tuple[bool, str]] | None = None,
        **kwargs: Any,
    ) -> tuple[bool, httpx.Response | None]:
        started = time.perf_counter()
        response: httpx.Response | None = None
        ok = False
        detail = ""
        status: int | None = None
        try:
            response = await self.client.request(method, path, **kwargs)
            status = response.status_code
            ok = status == expect
            detail = _summarize(response)
            if ok and validate is not None:
                ok, custom_detail = validate(response)
                detail = f"{detail} {custom_detail}".strip()
        except Exception as exc:  # noqa: BLE001 - smoke report must record any failure
            detail = f"{type(exc).__name__}: {exc}"
        self.record(family, name, ok, status, detail, int((time.perf_counter() - started) * 1000))
        return ok, response


def _validate_backfill_fulltext(response: httpx.Response) -> tuple[bool, str]:
    """批量补全文只允许命中跳过分支：冒烟不应真的提交下载任务。"""
    payload = response.json()
    if payload.get("queued"):
        return False, f"queued={len(payload['queued'])} 不应提交新任务"
    reasons = {str(item.get("reason")) for item in payload.get("skipped") or [] if isinstance(item, dict)}
    if "paper_not_found" not in reasons:
        return False, "缺少 paper_not_found 跳过记录"
    return True, f"skipped={','.join(sorted(reasons))}"


def _payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("items", "databases", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _write_report(run: SmokeRun, kb_id: str, started_at: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    passed = sum(1 for check in run.checks if check["ok"])
    failed = len(run.checks) - passed
    payload = {
        "started_at": started_at,
        "base_url": BASE_URL,
        "kb_id": kb_id,
        "passed": passed,
        "failed": failed,
        "checks": run.checks,
    }
    json_path = REPORT_DIR / f"smoke-acceptance-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# ResearchCompass 冒烟验收",
        "",
        f"- 开始时间：{started_at}",
        f"- 服务地址：{BASE_URL}",
        f"- 知识库：{kb_id or '-'}",
        f"- 结果：{passed} 通过 / {failed} 失败",
        "",
        "| 能力族 | 检查项 | 结果 | HTTP | 证据 | 耗时 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for check in run.checks:
        lines.append(
            "| {family} | {name} | {result} | {status} | {detail} | {duration} ms |".format(
                family=check["family"],
                name=check["name"],
                result="通过" if check["ok"] else "失败",
                status=check["status"] or "-",
                detail=str(check["detail"]).replace("|", "/"),
                duration=check["duration_ms"],
            )
        )
    md_path = REPORT_DIR / f"smoke-acceptance-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


async def _poll_agent_run(client: httpx.AsyncClient, run_id: str, timeout_seconds: int = 240) -> tuple[str, str]:
    deadline = time.time() + timeout_seconds
    status = "unknown"
    error = ""
    last_http = "no-response"
    while time.time() < deadline:
        response = await client.get(f"/api/agent/runs/{run_id}")
        last_http = str(response.status_code)
        if response.status_code == 404:
            await asyncio.sleep(2)
            continue
        if response.status_code == 200:
            payload = response.json()
            run_payload = payload.get("run") if isinstance(payload.get("run"), dict) else payload
            status = str(run_payload.get("status") or "unknown")
            error = str(run_payload.get("error_message") or run_payload.get("error") or "")
            if status in {"completed", "failed", "cancelled", "interrupted"}:
                return status, error
        await asyncio.sleep(2)
    return status, f"{error} last_http={last_http}".strip()


async def _resume_paused_queue(client: httpx.AsyncClient, *, thread_id: str) -> str:
    """上一次运行失败后线程队列会停在 paused 态，新请求只入队不派发。

    按产品语义点一次「继续」，返回派发出的 run_id；不可恢复时返回空串。
    """
    try:
        response = await client.post(
            f"/api/agent/thread/{thread_id}/requests/continue",
            params={"agent_slug": RESEARCH_COPILOT_AGENT_SLUG},
        )
    except Exception as exc:  # noqa: BLE001 - smoke report must record any failure
        print(f"queue resume request failed (error_type={type(exc).__name__})")
        return ""
    if response.status_code != 200:
        return ""
    return str(response.json().get("run_id") or "")


async def main() -> int:
    uid, password = _load_admin_credentials()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout) as client:
        run = SmokeRun(client)

        await run.check("system", "health", "GET", "/api/system/health", expect=200)
        ok, response = await run.check(
            "auth",
            "login",
            "POST",
            "/api/auth/token",
            data={"username": uid, "password": password},
        )
        if not ok or response is None:
            _write_report(run, "", started_at)
            return 1
        token = response.json().get("access_token")
        if not token:
            run.record("auth", "token-present", False, response.status_code, "响应缺少 access_token")
            _write_report(run, "", started_at)
            return 1
        client.headers["Authorization"] = f"Bearer {token}"
        await run.check("auth", "me", "GET", "/api/auth/me")

        temp_username = f"smoke_pwd_{int(time.time())}"
        temp_password = "SmokePass123!"
        temp_new_password = "SmokePass456!"
        temp_user_id = None
        try:
            ok, response = await run.check(
                "auth",
                "temp-user-create",
                "POST",
                "/api/auth/users",
                json={"username": temp_username, "password": temp_password, "role": "user"},
            )
            if ok and response is not None:
                temp_user_id = response.json().get("id")
            if temp_user_id:
                ok, response = await run.check(
                    "auth",
                    "temp-user-login",
                    "POST",
                    "/api/auth/token",
                    data={"username": temp_username, "password": temp_password},
                )
                temp_token = response.json().get("access_token") if ok and response is not None else ""
                if temp_token:
                    user_headers = {"Authorization": f"Bearer {temp_token}"}
                    await run.check(
                        "auth",
                        "password-change",
                        "POST",
                        "/api/auth/password",
                        headers=user_headers,
                        json={"current_password": temp_password, "new_password": temp_new_password},
                    )
                    await run.check(
                        "auth",
                        "old-token-rejected",
                        "GET",
                        "/api/auth/me",
                        headers=user_headers,
                        expect=401,
                    )
                    ok, response = await run.check(
                        "auth",
                        "new-password-login",
                        "POST",
                        "/api/auth/token",
                        data={"username": temp_username, "password": temp_new_password},
                    )
                    new_token = response.json().get("access_token") if ok and response is not None else ""
                    if new_token:
                        await run.check(
                            "auth",
                            "new-token-accepted",
                            "GET",
                            "/api/auth/me",
                            headers={"Authorization": f"Bearer {new_token}"},
                        )
                    await run.check(
                        "auth",
                        "old-password-rejected",
                        "POST",
                        "/api/auth/token",
                        data={"username": temp_username, "password": temp_password},
                        expect=401,
                    )
        finally:
            if temp_user_id:
                await run.check("auth", "temp-user-delete", "DELETE", f"/api/auth/users/{temp_user_id}")

        await run.check("onboarding", "status", "GET", "/api/research/onboarding")

        ok, response = await run.check("knowledge", "databases", "GET", "/api/knowledge/databases/accessible")
        databases = _payload_items(response.json() if response is not None else None)
        if not ok or not databases:
            _write_report(run, "", started_at)
            return 1
        database = None
        reference_database = None
        kb_id = ""
        best_total = 0
        smallest_total = None
        for item in databases:
            probe_kb = str(item.get("kb_id") or "")
            # pytest 夹具的知识库不参与选样：既不该当参照库被写入验收数据，也不该当报告库。
            if not probe_kb or str(item.get("name") or "").startswith(("pytest_", "py_test")):
                continue
            ok, probe = await run.check(
                "knowledge",
                "probe-papers",
                "GET",
                f"/api/research/databases/{probe_kb}/papers",
                params={"page": 1, "page_size": 1},
            )
            total = int(probe.json().get("total") or 0) if ok and probe is not None else 0
            if total > best_total:
                database = item
                kb_id = probe_kb
                best_total = total
            if smallest_total is None or total < smallest_total:
                reference_database = item
                smallest_total = total
        if database is None:
            run.record("papers", "has-paper", False, None, "所有可访问知识库都没有论文")
            _write_report(run, "", started_at)
            return 1
        report_kb = kb_id

        ok, response = await run.check(
            "papers", "list", "GET", f"/api/research/databases/{kb_id}/papers", params={"page": 1, "page_size": 5}
        )
        papers: list[dict[str, Any]] = []
        if response is not None:
            papers = response.json().get("items") or []
        if not papers:
            run.record("papers", "has-paper", False, None, "知识库没有可用于后续检查的论文")
            _write_report(run, report_kb, started_at)
            return 1
        paper = papers[0]
        paper_id = str(paper["paper_id"])
        await run.check("papers", "detail", "GET", f"/api/research/databases/{kb_id}/papers/{paper_id}")

        chunk: dict[str, Any] | None = None
        ok, response = await run.check(
            "papers",
            "chunks",
            "GET",
            f"/api/research/databases/{kb_id}/papers/{paper_id}/chunks",
            params={"limit": 3},
        )
        if response is not None:
            chunks = response.json().get("items") or []
            chunk = chunks[0] if chunks else None
        await run.check(
            "papers",
            "export-bibtex",
            "GET",
            f"/api/research/databases/{kb_id}/papers/export/bibtex",
            params={"paper_ids": paper_id},
        )
        full_text_paper = next((item for item in papers if item.get("has_fulltext")), None)
        backfill_ids = [str(full_text_paper["paper_id"])] if full_text_paper else []
        backfill_ids.append("paper-smoke-missing")
        await run.check(
            "papers",
            "backfill-fulltext",
            "POST",
            f"/api/research/databases/{kb_id}/papers/backfill-fulltext",
            json={"paper_ids": backfill_ids},
            validate=_validate_backfill_fulltext,
        )

        note_id = ""
        if chunk is not None:
            content = str(chunk.get("content") or "")
            end_offset = min(20, len(content))
            try:
                ok, response = await run.check(
                    "notes",
                    "create",
                    "POST",
                    f"/api/research/databases/{kb_id}/papers/{paper_id}/notes",
                    json={
                        "content": "冒烟验收笔记",
                        "quote": content[:end_offset],
                        "chunk_id": chunk["chunk_id"],
                        "start_offset": 0,
                        "end_offset": end_offset,
                    },
                )
                if ok and response is not None:
                    note_id = str(response.json().get("note_id") or "")
                await run.check("notes", "list", "GET", f"/api/research/databases/{kb_id}/papers/{paper_id}/notes")
                if note_id:
                    await run.check(
                        "notes",
                        "update",
                        "PATCH",
                        f"/api/research/databases/{kb_id}/papers/{paper_id}/notes/{note_id}",
                        json={"content": "冒烟验收笔记（已更新）"},
                    )
                    await run.check(
                        "notes",
                        "export",
                        "GET",
                        f"/api/research/databases/{kb_id}/papers/{paper_id}/notes/export",
                    )
            finally:
                if note_id:
                    await run.check(
                        "notes",
                        "delete",
                        "DELETE",
                        f"/api/research/databases/{kb_id}/papers/{paper_id}/notes/{note_id}",
                    )
        else:
            run.record("notes", "skip", True, None, "该论文没有分块，跳过笔记/批注检查")

        await run.check("screening", "counts", "GET", f"/api/research/databases/{kb_id}/papers/screening")
        await run.check("screening", "export", "GET", f"/api/research/databases/{kb_id}/papers/screening/export")

        ok, response = await run.check(
            "projects", "list", "GET", f"/api/research/databases/{kb_id}/projects", params={"status": "active"}
        )
        projects = (response.json().get("items") or []) if response is not None else []
        if projects:
            project_id = str(projects[0]["project_id"])
            await run.check("projects", "detail", "GET", f"/api/research/projects/{project_id}")
            await run.check("projects", "plan", "GET", f"/api/research/projects/{project_id}/plan")
        else:
            run.record("projects", "detail-skip", True, None, "没有进行中的项目，跳过详情检查")

        await run.check(
            "search",
            "local_hybrid",
            "POST",
            f"/api/research/databases/{kb_id}/search",
            json={"query": "RAG 评估方法有哪些？", "retrieval_mode": "local_hybrid", "top_k": 3},
            timeout=180,
        )
        await run.check("synthesis", "list", "GET", f"/api/research/databases/{kb_id}/syntheses")
        ok, graph_response = await run.check(
            "graph", "network", "GET", f"/api/research/databases/{kb_id}/academic-graph"
        )
        graph_nodes = (graph_response.json().get("nodes") or []) if graph_response is not None else []
        if graph_nodes:
            await run.check(
                "graph",
                "export",
                "GET",
                f"/api/research/databases/{kb_id}/academic-graph/export",
                params={"format": "csv", "limit": 50},
            )
        else:
            run.record("graph", "export-skip", True, None, "图谱为空，跳过导出")
        await run.check("insight", "trends", "GET", f"/api/research/databases/{kb_id}/trends")
        await run.check("insight", "opportunities", "GET", f"/api/research/databases/{kb_id}/opportunities")

        paper_ids = ",".join(str(item["paper_id"]) for item in papers[:2])
        await run.check(
            "deliverables",
            "cards",
            "GET",
            f"/api/research/databases/{kb_id}/papers/cards",
            params={"format": "markdown", "paper_ids": paper_ids},
        )
        if len(papers) >= 2:
            await run.check(
                "deliverables",
                "matrix",
                "GET",
                f"/api/research/databases/{kb_id}/comparison-matrix",
                params={"format": "markdown", "paper_ids": paper_ids},
            )
        else:
            run.record("deliverables", "matrix-skip", True, None, "少于 2 篇论文，跳过对比矩阵")
        if graph_nodes:
            await run.check(
                "deliverables", "citation-insight", "GET", f"/api/research/databases/{kb_id}/citation-insight"
            )
        else:
            run.record("deliverables", "citation-insight-skip", True, None, "图谱为空，跳过引用洞察")
        await run.check("deliverables", "manifest", "GET", f"/api/research/databases/{kb_id}/export-manifest")
        await run.check("deliverables", "duplicates", "GET", f"/api/research/databases/{kb_id}/duplicates")
        await run.check("deliverables", "archived", "GET", f"/api/research/databases/{kb_id}/papers/archived")
        await run.check(
            "deliverables",
            "trends-export",
            "GET",
            f"/api/research/databases/{kb_id}/exports/trends",
            params={"format": "json"},
        )

        await run.check("evaluation", "datasets", "GET", f"/api/evaluation/databases/{kb_id}/datasets")
        await run.check("evaluation", "runs", "GET", f"/api/evaluation/databases/{kb_id}/runs")
        await run.check("evaluation", "experiments", "GET", f"/api/evaluation/databases/{kb_id}/experiments")
        await run.check("analysis", "evaluations", "GET", f"/api/research/databases/{kb_id}/paper-analysis-evaluations")
        await run.check("user-studies", "list", "GET", f"/api/research/databases/{kb_id}/user-studies")
        await run.check(
            "discovery",
            "multi-source",
            "GET",
            f"/api/research/databases/{kb_id}/external-papers/search",
            params={"query": "retrieval augmented generation", "limit": 3},
            timeout=180,
        )

        reference_kb = str(reference_database["kb_id"]) if reference_database else kb_id
        await run.check(
            "references",
            "import-idempotent",
            "POST",
            f"/api/research/databases/{reference_kb}/papers/import-references",
            json={"content": SMOKE_REFERENCE, "format": "bibtex"},
            validate=lambda response: (
                int(response.json().get("imported_count") or 0) + int(response.json().get("skipped_count") or 0) >= 1,
                f"imported={response.json().get('imported_count')} skipped={response.json().get('skipped_count')}",
            ),
        )

        ok, response = await run.check(
            "ai",
            "copilot-thread",
            "POST",
            "/api/research/copilot/thread",
            json={"kb_id": kb_id, "surface": "library"},
        )
        thread_id = ""
        if ok and response is not None:
            thread_id = str((response.json().get("thread") or {}).get("id") or "")
        if thread_id:
            ok, response = await run.check(
                "ai",
                "run-submit",
                "POST",
                "/api/agent/runs",
                json={
                    "agent_slug": RESEARCH_COPILOT_AGENT_SLUG,
                    "thread_id": thread_id,
                    "query": "请用一句话说明当前知识库的论文总数，并说明你调用了哪个工具。",
                },
            )
            run_id = str(response.json().get("run_id") or "") if ok and response is not None else ""
            resumed = False
            if not run_id and response is not None and response.json().get("status") == "queued":
                run_id = await _resume_paused_queue(client, thread_id=thread_id)
                resumed = bool(run_id)
            if run_id:
                status, error = await _poll_agent_run(client, run_id)
                detail = f"status={status} {error}".strip()
                if resumed:
                    detail = f"{detail} resumed=paused-queue".strip()
                run.record(
                    "ai",
                    "run-complete",
                    status == "completed",
                    200,
                    detail,
                )
            else:
                run.record("ai", "run-complete", False, None, "未拿到 run_id")
        else:
            run.record("ai", "run-complete", False, None, "未拿到研究助手会话")

    md_path = _write_report(run, report_kb, started_at)
    failed = sum(1 for check in run.checks if not check["ok"])
    print(f"REPORT {md_path}")
    print(f"SUMMARY passed={len(run.checks) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
