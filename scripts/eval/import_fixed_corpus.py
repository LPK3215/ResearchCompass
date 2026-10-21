"""按固定语料清单批量提交论文导入任务。

用法（在 backend 目录执行）：
    uv run --no-sync --env-file ../.env python ../scripts/eval/import_fixed_corpus.py \
        --kb-id kb_xxx [--limit 5] [--interval 2.5] [--dry-run]

脚本只提交导入任务，不等待解析完成；进度在任务中心查看。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = Path(__file__).resolve().parent / "fixed_corpus.json"


def _load_admin_credentials() -> tuple[str, str]:
    uid = os.getenv("ADMIN_UID", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if uid and password:
        return uid, password
    env_path = ROOT / "saves/local/dev_admin.env"
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values["ADMIN_UID"], values["ADMIN_PASSWORD"]


def main() -> int:
    parser = argparse.ArgumentParser(description="导入固定语料清单")
    parser.add_argument("--kb-id", required=True, help="目标知识库 ID")
    parser.add_argument("--limit", type=int, default=0, help="最多导入多少篇，0 表示全部")
    parser.add_argument("--interval", type=float, default=2.5, help="两次提交之间的间隔秒数")
    parser.add_argument("--dry-run", action="store_true", help="只打印清单，不提交")
    parser.add_argument("--base-url", default="http://127.0.0.1:5050")
    args = parser.parse_args()

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    papers = corpus["papers"][: args.limit or None]
    if args.dry_run:
        for paper in papers:
            print(paper["identifier"], paper["title"])
        return 0

    uid, password = _load_admin_credentials()
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60.0) as client:
        response = client.post("/api/auth/token", data={"username": uid, "password": password})
        response.raise_for_status()
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

        kb_info = client.get(f"/api/knowledge/databases/{args.kb_id}")
        if kb_info.status_code == 200:
            params = kb_info.json().get("additional_params") or {}
            preset = str(params.get("chunk_preset_id") or "")
            if preset and preset != "academic":
                print(
                    f"WARN 当前知识库 chunk_preset_id={preset}，固定语料建议使用 academic 预设；"
                    "若解析服务未启动，导入任务会停在“准备解析外部论文”。"
                )

        submitted = 0
        skipped = 0
        failed = 0
        for index, paper in enumerate(papers, start=1):
            identifier = paper["identifier"]
            try:
                result = client.post(
                    f"/api/research/databases/{args.kb_id}/external-papers/import",
                    json={"identifier": identifier},
                )
            except Exception as exc:  # noqa: BLE001 - 批量脚本记录失败后继续
                failed += 1
                print(f"[{index}/{len(papers)}] FAIL {identifier} {type(exc).__name__}: {exc}")
                continue
            if result.status_code == 200:
                submitted += 1
                payload = result.json()
                print(
                    f"[{index}/{len(papers)}] OK {identifier} "
                    f"status={payload.get('status')} task={payload.get('task_id')}"
                )
            elif result.status_code == 409:
                skipped += 1
                print(f"[{index}/{len(papers)}] SKIP {identifier} {result.json().get('detail')}")
            else:
                failed += 1
                print(f"[{index}/{len(papers)}] FAIL {identifier} HTTP {result.status_code} {result.text[:200]}")
            time.sleep(max(args.interval, 0))

    print(f"SUMMARY submitted={submitted} skipped={skipped} failed={failed} total={len(papers)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
