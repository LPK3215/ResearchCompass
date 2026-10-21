"""固定语料综述证据覆盖基线：对跨论文问题跑证据约束综述，记录引用覆盖率。

用法（在 backend 目录执行）：
    uv run --no-sync python ../scripts/eval/fixed_corpus_synthesis.py --kb-id kb_xxx

产品流程说明：证据约束综述要求检索到的每个证据片段都已在「研究证据」中录入并核验为
verified，否则综述会以 synthesis_insufficient_evidence 失败并保留检索快照。本脚本按
真实产品流程操作：先跑综述，失败时读取该次运行的检索快照，把快照里的片段录入并核验，
再重跑，直到拿到 coverage。

输出：控制台表格 + test-results/fixed-corpus-synthesis-<时间戳>.json / .md。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from corpus_common import load_admin_credentials, load_corpus

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "test-results"


def _render_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    """把综述证据覆盖结果渲染成 Markdown 表格。"""
    lines = [
        "# 固定语料综述证据覆盖基线",
        "",
        f"- 知识库：`{summary['kb_id']}`",
        f"- 模型：`{summary['model_spec']}`，重排：`{summary['reranker_model']}`",
        f"- 生成时间：{summary['generated_at']}",
        f"- 问题总数：{summary['questions_total']}（成功 {summary['questions_succeeded']}，"
        f"失败 {summary['questions_failed']}）",
        f"- 平均引用覆盖率：{summary['avg_citation_coverage']}",
        "",
        "| 题目 | 金标准论文数 | 检索论文 | 引用论文 | 引用覆盖率 | 结论数 | 耗时(ms) | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in results:
        coverage = record.get("coverage") or {}
        lines.append(
            "| {id} | {gold} | {retrieved} | {distinct} | {ratio} | {conclusions} | {elapsed} | {status} |".format(
                id=record["id"],
                gold=len(record.get("gold_paper_identifiers") or []),
                retrieved=coverage.get("retrieved_papers"),
                distinct=coverage.get("distinct_papers"),
                ratio=coverage.get("citation_coverage_ratio"),
                conclusions=coverage.get("supported_conclusions"),
                elapsed=record.get("elapsed_ms"),
                status=record.get("status"),
            )
        )
    return "\n".join(lines) + "\n"


def _evidence_map(client: httpx.Client, kb_id: str) -> dict[str, dict[str, Any]]:
    """取库内已有证据：source_chunk_id → 证据记录。"""
    response = client.get(f"/api/research/databases/{kb_id}/evidence")
    if response.status_code != 200:
        return {}
    items = response.json().get("items") or []
    return {str(item.get("source_chunk_id")): item for item in items}


def _verify_chunks(client: httpx.Client, kb_id: str, chunk_ids: list[str]) -> dict[str, int]:
    """把检索到的片段录入为证据并核验（产品要求的证据前置条件）。"""
    known = _evidence_map(client, kb_id)
    created = 0
    verified = 0
    failed = 0
    for chunk_id in chunk_ids:
        record = known.get(chunk_id)
        if record is None:
            response = client.post(f"/api/research/databases/{kb_id}/evidence", json={"chunk_id": chunk_id})
            if response.status_code != 201:
                failed += 1
                continue
            record = response.json()
            created += 1
        if str(record.get("status")) == "verified":
            continue
        response = client.post(
            f"/api/research/evidence/{record.get('evidence_id')}/transition",
            json={"status": "verified", "reason": "固定语料基线自动核验"},
        )
        if response.status_code == 200:
            verified += 1
        else:
            failed += 1
    return {"created": created, "verified": verified, "failed": failed}


def _corpus_chunk_ids(client: httpx.Client, kb_id: str) -> list[str]:
    """列出知识库内全部 chunk id（逐篇取分页结果）。"""
    papers: list[dict[str, Any]] = []
    page = 1
    while True:
        response = client.get(
            f"/api/research/databases/{kb_id}/papers", params={"page": page, "page_size": 100}
        )
        if response.status_code != 200:
            break
        items = response.json().get("items") or []
        papers.extend(items)
        if len(items) < 100:
            break
        page += 1

    chunk_ids: list[str] = []
    for paper in papers:
        if not paper.get("file_id"):
            continue
        offset = 0
        while True:
            response = client.get(
                f"/api/research/databases/{kb_id}/papers/{paper.get('paper_id')}/chunks",
                params={"offset": offset, "limit": 200},
            )
            if response.status_code != 200:
                break
            payload = response.json()
            for chunk in payload.get("items") or []:
                chunk_id = str(chunk.get("chunk_id") or "")
                if chunk_id:
                    chunk_ids.append(chunk_id)
            offset += len(payload.get("items") or [])
            if not payload.get("has_more"):
                break
    return chunk_ids


def _verify_corpus_evidence(client: httpx.Client, kb_id: str) -> dict[str, int]:
    """把整库片段录入并核验为证据，保证综述快照可校验。

    综述的引用校验要求检索到的每个片段都已核验，而每次检索的快照会随改写结果浮动，
    逐次补核验容易反复失败；先做一次整库核验，再跑综述。
    """
    chunk_ids = _corpus_chunk_ids(client, kb_id)
    known = _evidence_map(client, kb_id)
    todo = [chunk_id for chunk_id in chunk_ids if chunk_id not in known]
    stats = {"chunks": len(chunk_ids), "created": 0, "verified": 0, "failed": 0}
    for chunk_id in todo:
        response = client.post(f"/api/research/databases/{kb_id}/evidence", json={"chunk_id": chunk_id})
        if response.status_code != 201:
            stats["failed"] += 1
            continue
        stats["created"] += 1
        evidence_id = (response.json() or {}).get("evidence_id")
        transition = client.post(
            f"/api/research/evidence/{evidence_id}/transition",
            json={"status": "verified", "reason": "固定语料基线整库核验"},
        )
        if transition.status_code == 200:
            stats["verified"] += 1
        else:
            stats["failed"] += 1
    return stats


def _snapshot_chunk_ids(record: dict[str, Any]) -> list[str]:
    """从失败运行的检索快照里取出全部证据片段 id。"""
    snapshot = record.get("retrieval_snapshot") or {}
    chunk_ids: list[str] = []
    for paper in snapshot.get("papers") or []:
        for evidence in paper.get("evidence") or []:
            chunk_id = str(evidence.get("chunk_id") or "")
            if chunk_id:
                chunk_ids.append(chunk_id)
    return chunk_ids


def _run_once(
    client: httpx.Client,
    kb_id: str,
    question: dict[str, Any],
    *,
    top_k: int,
    recall_top_k: int,
    model_spec: str,
    reranker_model: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """跑一次综述并等到终态。"""
    started = time.perf_counter()
    created = client.post(
        f"/api/research/databases/{kb_id}/syntheses",
        json={
            "query": question["question"],
            "top_k": top_k,
            "recall_top_k": recall_top_k,
            "model_spec": model_spec,
            "reranker_model": reranker_model,
        },
    )
    if created.status_code != 200:
        return {
            "status": f"enqueue_failed_{created.status_code}",
            "error": created.text[:400],
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # 接口直接返回 {"run_id": ..., "task_id": ..., "status": ...}，不做 data 包装。
    payload = created.json()
    run_id = payload.get("run_id") or (payload.get("data") or {}).get("run_id")
    if not run_id:
        return {
            "status": "enqueue_failed_no_run_id",
            "error": created.text[:400],
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    deadline = time.time() + timeout_seconds
    record: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/api/research/synthesis-runs/{run_id}")
        if response.status_code != 200:
            record = {"status": f"poll_failed_{response.status_code}"}
        else:
            # 运行详情同样直接返回记录对象。
            body = response.json()
            record = body if isinstance(body, dict) else {}
            if isinstance(record.get("data"), dict):
                record = record["data"]
        if record.get("status") in {"success", "failed", "cancelled"}:
            break
        time.sleep(8)

    return {
        "run_id": run_id,
        "status": record.get("status"),
        "error_type": record.get("error_type"),
        "error_message": record.get("error_message"),
        "stage_timings": record.get("stage_timings"),
        "record": record,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


def _run_synthesis(
    client: httpx.Client,
    kb_id: str,
    question: dict[str, Any],
    *,
    top_k: int,
    recall_top_k: int,
    model_spec: str,
    reranker_model: str,
    timeout_seconds: int,
    max_attempts: int,
) -> dict[str, Any]:
    """按产品流程跑综述：证据未核验就补核验后重试。"""
    started = time.perf_counter()
    attempts: list[dict[str, Any]] = []
    evidence_stats = {"created": 0, "verified": 0, "failed": 0}

    for attempt in range(1, max_attempts + 1):
        outcome = _run_once(
            client,
            kb_id,
            question,
            top_k=top_k,
            recall_top_k=recall_top_k,
            model_spec=model_spec,
            reranker_model=reranker_model,
            timeout_seconds=timeout_seconds,
        )
        record = outcome.pop("record", {})
        attempts.append(
            {
                "attempt": attempt,
                "status": outcome.get("status"),
                "error_type": outcome.get("error_type"),
                "error_message": outcome.get("error_message"),
                "elapsed_ms": outcome.get("elapsed_ms"),
            }
        )

        if outcome.get("status") == "success":
            result = record.get("result") or {}
            return {
                "id": question["id"],
                "question": question["question"],
                "gold_paper_identifiers": question.get("gold_paper_identifiers") or [],
                "run_id": outcome.get("run_id"),
                "status": "success",
                "attempts": attempts,
                "evidence": evidence_stats,
                "coverage": result.get("coverage") or {},
                "sources": [
                    {"paper_id": source.get("paper_id"), "title": source.get("title")}
                    for source in (result.get("sources") or [])
                ],
                "stage_timings": outcome.get("stage_timings"),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }

        if outcome.get("error_type") != "synthesis_insufficient_evidence":
            return {
                "id": question["id"],
                "question": question["question"],
                "gold_paper_identifiers": question.get("gold_paper_identifiers") or [],
                "run_id": outcome.get("run_id"),
                "status": outcome.get("status"),
                "error_type": outcome.get("error_type"),
                "error_message": outcome.get("error_message"),
                "attempts": attempts,
                "evidence": evidence_stats,
                "coverage": {},
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }

        chunk_ids = _snapshot_chunk_ids(record)
        stats = _verify_chunks(client, kb_id, chunk_ids)
        evidence_stats["created"] += stats["created"]
        evidence_stats["verified"] += stats["verified"]
        evidence_stats["failed"] += stats["failed"]
        attempts[-1]["verified_chunks"] = len(chunk_ids)

    return {
        "id": question["id"],
        "question": question["question"],
        "gold_paper_identifiers": question.get("gold_paper_identifiers") or [],
        "status": "exhausted_attempts",
        "attempts": attempts,
        "evidence": evidence_stats,
        "coverage": {},
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="固定语料综述证据覆盖基线")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--question-ids", default="", help="只跑这些题号，逗号分隔；默认跑全部跨论文题")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--recall-top-k", type=int, default=50)
    parser.add_argument("--model-spec", default="workbuddy:deepseek-v4.1-flash")
    parser.add_argument("--reranker-model", default="local-embedding:BAAI/bge-reranker-base")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--skip-corpus-verify",
        action="store_true",
        help="跳过整库证据核验（库内片段已全部核验时使用）",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:5050")
    args = parser.parse_args()

    corpus = load_corpus()
    questions = corpus["questions"]
    if args.question_ids:
        wanted = {value.strip() for value in args.question_ids.split(",") if value.strip()}
        questions = [question for question in questions if question["id"] in wanted]
    else:
        questions = [
            question for question in questions if len(question.get("gold_paper_identifiers") or []) >= 2
        ]

    uid, password = load_admin_credentials()
    results: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=httpx.Timeout(args.timeout_seconds, connect=10.0)
    ) as client:
        token = client.post(
            "/api/auth/token", data={"username": uid, "password": password}
        ).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        corpus_evidence: dict[str, int] = {}
        if not args.skip_corpus_verify:
            corpus_evidence = _verify_corpus_evidence(client, args.kb_id)
            print(f"整库证据核验：{json.dumps(corpus_evidence, ensure_ascii=False)}")

        for question in questions:
            record = _run_synthesis(
                client,
                args.kb_id,
                question,
                top_k=args.top_k,
                recall_top_k=args.recall_top_k,
                model_spec=args.model_spec,
                reranker_model=args.reranker_model,
                timeout_seconds=args.timeout_seconds,
                max_attempts=args.max_attempts,
            )
            results.append(record)
            coverage = record.get("coverage") or {}
            print(
                f"[{record['status']}] {record['id']} "
                f"coverage={coverage.get('citation_coverage_ratio')} "
                f"papers={coverage.get('distinct_papers')}/{coverage.get('retrieved_papers')} "
                f"attempts={len(record.get('attempts') or [])} "
                f"{record['elapsed_ms']} ms | {record['question']}"
            )

    succeeded = [record for record in results if record["status"] == "success"]
    ratios = [
        (record.get("coverage") or {}).get("citation_coverage_ratio")
        for record in succeeded
        if (record.get("coverage") or {}).get("citation_coverage_ratio") is not None
    ]
    summary = {
        "kb_id": args.kb_id,
        "model_spec": args.model_spec,
        "reranker_model": args.reranker_model,
        "corpus_evidence": corpus_evidence,
        "questions_total": len(results),
        "questions_succeeded": len(succeeded),
        "questions_failed": len(results) - len(succeeded),
        "avg_citation_coverage": round(sum(ratios) / len(ratios), 4) if ratios else 0.0,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = REPORT_DIR / f"fixed-corpus-synthesis-{stamp}.json"
    out_path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"REPORT {out_path}")
    md_path = REPORT_DIR / f"fixed-corpus-synthesis-{stamp}.md"
    md_path.write_text(_render_markdown(summary, results), encoding="utf-8")
    print(f"REPORT {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
