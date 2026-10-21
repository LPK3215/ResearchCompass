"""固定语料消融实验：只切换「是否启用重排序」，其余检索参数完全一致。

用法（backend 目录）：
    uv run --no-sync python ../scripts/eval/fixed_corpus_ablation.py --kb-id kb_xxx

说明：
- 复用已有评估数据集（默认取知识库下第一个构建完成的数据集）。
- shared_config 里配置 answer_llm（生成答案）与 judge_llm（裁判），否则评测只会算检索指标。
- 两个变体只差 use_reranker / reranker_model，其余参数（search_mode、recall_top_k、
  final_top_k、similarity_threshold）保持一致，保证是受控对比。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from corpus_common import load_admin_credentials

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "test-results"

BASE_RETRIEVAL = {
    "search_mode": "hybrid",
    "recall_top_k": 50,
    "final_top_k": 10,
    "similarity_threshold": 0.0,
    "include_distances": True,
}


def _variants(reranker_model: str) -> list[dict[str, Any]]:
    """两个变体只差重排序开关。"""
    return [
        {
            "name": "baseline_reranker",
            "retrieval_config": {
                **BASE_RETRIEVAL,
                "use_reranker": True,
                "reranker_model": reranker_model,
            },
        },
        {
            "name": "no_reranker",
            "retrieval_config": {
                **BASE_RETRIEVAL,
                "use_reranker": False,
                "reranker_model": "",
            },
        },
    ]


def _pick_dataset(client: httpx.Client, kb_id: str) -> str:
    """取知识库下第一个构建完成的评估数据集。"""
    payload = client.get(f"/api/evaluation/databases/{kb_id}/datasets").json()
    datasets = payload.get("data") or []
    ready = [d for d in datasets if (d.get("build_metadata") or {}).get("status") == "completed"]
    if not ready:
        raise SystemExit("没有构建完成的评估数据集，请先生成")
    return ready[0]["dataset_id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="固定语料消融实验")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--dataset-id", default="")
    parser.add_argument("--answer-llm", default="workbuddy:deepseek-v4.1-flash")
    parser.add_argument("--judge-llm", default="workbuddy:glm-5.3")
    parser.add_argument("--reranker-model", default="local-embedding:BAAI/bge-reranker-base")
    parser.add_argument("--base-url", default="http://127.0.0.1:5050")
    args = parser.parse_args()

    uid, password = load_admin_credentials()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = REPORT_DIR / f"fixed-corpus-ablation-{stamp}.log"

    def log(message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=httpx.Timeout(3600.0, connect=10.0)
    ) as client:
        token = client.post(
            "/api/auth/token", data={"username": uid, "password": password}
        ).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        dataset_id = args.dataset_id or _pick_dataset(client, args.kb_id)
        log(f"dataset_id={dataset_id}")

        created = client.post(
            f"/api/evaluation/databases/{args.kb_id}/experiments",
            json={
                "dataset_id": dataset_id,
                "name": "固定语料消融：重排序开关",
                "description": (
                    f"baseline_reranker=hybrid+重排({args.reranker_model})；"
                    "no_reranker=同一参数但关闭重排；其余参数一致"
                ),
                "baseline_variant_index": 0,
                "shared_config": {"answer_llm": args.answer_llm, "judge_llm": args.judge_llm},
                "variants": _variants(args.reranker_model),
            },
        )
        if created.status_code != 200:
            log(f"创建实验失败 {created.status_code}: {created.text[:600]}")
            return 1
        experiment_id = (created.json().get("data") or {}).get("experiment_id")
        log(f"experiment_id={experiment_id}")

        report: dict[str, Any] = {}
        deadline = time.time() + 3600
        while time.time() < deadline:
            response = client.get(f"/api/evaluation/databases/{args.kb_id}/experiments/{experiment_id}")
            report = response.json().get("data") or {}
            log(
                f"status={report.get('status')} "
                f"variants={report.get('completed_variants')}/{report.get('total_variants')}"
            )
            if report.get("status") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(20)

    out_path = REPORT_DIR / f"fixed-corpus-ablation-{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"REPORT {out_path}")

    for variant in report.get("variants") or []:
        metrics = variant.get("metrics") or {}
        log(
            f"variant={variant.get('name')} "
            f"recall@1={metrics.get('recall@1')} recall@3={metrics.get('recall@3')} "
            f"recall@5={metrics.get('recall@5')} recall@10={metrics.get('recall@10')} "
            f"answer_correctness={metrics.get('answer_correctness')} "
            f"answer_coverage={variant.get('answer_coverage')} "
            f"overall={variant.get('overall_score')} overall_metric={variant.get('overall_metric')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
