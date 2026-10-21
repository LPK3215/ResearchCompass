"""固定语料检索基线：对 fixed_corpus.json 的金标准问题跑检索，记录命中情况。

用法（在 backend 目录执行）：
    uv run --no-sync --env-file ../.env python ../scripts/eval/fixed_corpus_baseline.py \
        --kb-id kb_xxx [--top-k 5] [--retrieval-mode local_hybrid]

输出：控制台表格 + test-results/fixed-corpus-baseline-<时间戳>.json。
只有金标准论文全部入库的问题才计入统计，未入库的题目标为 incomplete（不臆造分数）。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from corpus_common import load_admin_credentials, load_corpus, normalize_title

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "test-results"


def _arxiv_id(identifier: str) -> str:
    """取出 arXiv 论文号（去掉前缀与版本号后缀）。"""
    raw = str(identifier or "").split(":", 1)[-1].strip()
    return raw.split("v", 1)[0] if raw else ""


def _kb_papers(client: httpx.Client, kb_id: str) -> tuple[dict[str, str], set[str]]:
    """列出知识库内已入库论文：arXiv 论文号 → 标题，以及归一化标题集合。"""
    by_arxiv: dict[str, str] = {}
    titles: set[str] = set()
    page = 1
    while True:
        response = client.get(
            f"/api/research/databases/{kb_id}/papers",
            params={"page": page, "page_size": 100},
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") or []
        for item in items:
            title = str(item.get("title") or "")
            titles.add(normalize_title(title))
            external_ids = item.get("external_ids") or {}
            arxiv_id = _arxiv_id(str(external_ids.get("ArXiv") or ""))
            if arxiv_id:
                by_arxiv[arxiv_id] = title
        if len(items) < 100:
            break
        page += 1
    return by_arxiv, titles


def _gold_title(
    identifier: str,
    papers_by_id: dict[str, Any],
    by_arxiv: dict[str, str],
    titles: set[str],
) -> str | None:
    """定位金标准论文在库内的标题：优先按 arXiv 论文号，回退到标题归一化匹配。

    语料里的标题是人工简写，库内标题来自上游元数据，两者可能不一致；按论文号匹配
    才能判断「是否已入库」，用库内真实标题做排序比对才不会被标题差异干扰。
    """
    arxiv_id = _arxiv_id(identifier)
    if arxiv_id and arxiv_id in by_arxiv:
        return by_arxiv[arxiv_id]
    corpus_title = str(papers_by_id[identifier]["title"])
    if normalize_title(corpus_title) in titles:
        return corpus_title
    return None


def _run_question(
    client: httpx.Client,
    kb_id: str,
    question: dict[str, Any],
    *,
    top_k: int,
    retrieval_mode: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/research/databases/{kb_id}/search",
        json={"query": question["question"], "retrieval_mode": retrieval_mode, "top_k": top_k},
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items") or []
    return {
        "id": question["id"],
        "question": question["question"],
        "gold_paper_identifiers": question.get("gold_paper_identifiers") or [],
        "gold_points": question.get("gold_points") or [],
        "retrieved_titles": [str(item.get("title") or "") for item in items],
        "retrieved_scores": [
            {
                "title": str(item.get("title") or ""),
                "ranking_score": item.get("ranking_score"),
                "rerank": (item.get("scores") or {}).get("rerank"),
            }
            for item in items
        ],
    }


def _gold_titles(
    question: dict[str, Any],
    papers_by_id: dict[str, Any],
    by_arxiv: dict[str, str],
    titles: set[str],
) -> tuple[list[str], list[str]]:
    """返回一道问题的金标准标题（归一化）与未入库的论文标识。"""
    gold_titles: list[str] = []
    missing: list[str] = []
    for identifier in question.get("gold_paper_identifiers") or []:
        if identifier not in papers_by_id:
            missing.append(identifier)
            continue
        gold_title = _gold_title(identifier, papers_by_id, by_arxiv, titles)
        if gold_title is None:
            missing.append(identifier)
            continue
        gold_titles.append(normalize_title(gold_title))
    return gold_titles, missing


def _render_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    """把基线结果渲染成可直接归档的 Markdown 表格。"""
    lines = [
        "# 固定语料检索基线",
        "",
        f"- 知识库：`{summary['kb_id']}`",
        f"- 检索模式：`{summary['retrieval_mode']}`，top_k={summary['top_k']}",
        f"- 生成时间：{summary['generated_at']}",
        f"- 题目总数：{summary['questions_total']}（计分 {summary['questions_scored']}，"
        f"金标准未入库 {summary['questions_incomplete']}）",
        f"- hit@1={summary['hit_at_1']} hit@3={summary['hit_at_3']} hit@5={summary['hit_at_5']} "
        f"MRR={summary['mrr']}",
        "",
        "| 题目 | 金标准命中名次 | 耗时(ms) | 状态 |",
        "| --- | --- | --- | --- |",
    ]
    for record in results:
        rank = record["first_gold_rank"] if record["first_gold_rank"] is not None else "-"
        state = "计分" if record["complete"] else f"未入库 {len(record['missing_gold_papers'])} 篇"
        lines.append(f"| {record['id']} | {rank} | {record['elapsed_ms']} | {state} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="固定语料检索基线")
    parser.add_argument("--kb-id", required=True, help="目标知识库 ID")
    parser.add_argument("--top-k", type=int, default=5, help="每个问题取前 K 条")
    parser.add_argument("--retrieval-mode", default="local_hybrid", help="检索模式")
    parser.add_argument("--base-url", default="http://127.0.0.1:5050")
    args = parser.parse_args()

    corpus = load_corpus()
    papers_by_id = {paper["identifier"]: paper for paper in corpus["papers"]}
    uid, password = load_admin_credentials()

    results: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        token_response = client.post("/api/auth/token", data={"username": uid, "password": password})
        token_response.raise_for_status()
        client.headers["Authorization"] = f"Bearer {token_response.json()['access_token']}"

        by_arxiv, kb_titles = _kb_papers(client, args.kb_id)
        print(f"kb={args.kb_id} 已入库论文 {len(kb_titles)} 篇；问题 {len(corpus['questions'])} 道")

        for question in corpus["questions"]:
            gold_titles, missing = _gold_titles(question, papers_by_id, by_arxiv, kb_titles)

            started = time.perf_counter()
            record = _run_question(
                client,
                args.kb_id,
                question,
                top_k=args.top_k,
                retrieval_mode=args.retrieval_mode,
            )
            record["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            record["missing_gold_papers"] = missing

            ranks = []
            for gold_title in gold_titles:
                for index, title in enumerate(record["retrieved_titles"], start=1):
                    if normalize_title(title) == gold_title:
                        ranks.append(index)
                        break
            record["gold_ranks"] = sorted(ranks)
            record["hit"] = bool(ranks)
            record["first_gold_rank"] = min(ranks) if ranks else None
            record["complete"] = not missing
            results.append(record)

            flag = "OK " if record["hit"] else "MISS"
            state = "" if record["complete"] else f" incomplete(missing={len(missing)})"
            print(
                f"[{flag}] {question['id']} rank={record['first_gold_rank']} "
                f"{record['elapsed_ms']} ms{state} | {question['question']}"
            )

    scored = [record for record in results if record["complete"]]
    hit_at = {
        k: sum(1 for record in scored if record["first_gold_rank"] is not None and record["first_gold_rank"] <= k)
        for k in (1, 3, 5)
    }
    reciprocal = [1.0 / record["first_gold_rank"] for record in scored if record["first_gold_rank"]]
    summary = {
        "kb_id": args.kb_id,
        "top_k": args.top_k,
        "retrieval_mode": args.retrieval_mode,
        "questions_total": len(results),
        "questions_scored": len(scored),
        "questions_incomplete": len(results) - len(scored),
        "hit_at_1": hit_at[1],
        "hit_at_3": hit_at[3],
        "hit_at_5": hit_at[5],
        "mrr": round(sum(reciprocal) / len(reciprocal), 4) if reciprocal else 0.0,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = REPORT_DIR / f"fixed-corpus-baseline-{stamp}.json"
    out_path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"REPORT {out_path}")
    md_path = REPORT_DIR / f"fixed-corpus-baseline-{stamp}.md"
    md_path.write_text(_render_markdown(summary, results), encoding="utf-8")
    print(f"REPORT {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
