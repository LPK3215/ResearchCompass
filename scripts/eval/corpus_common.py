"""固定语料评测脚本的公共辅助：语料读取、管理员凭据与标题归一化。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = Path(__file__).resolve().parent / "fixed_corpus.json"


def load_corpus() -> dict[str, Any]:
    """读取固定语料清单。"""
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def load_admin_credentials() -> tuple[str, str]:
    """读取管理员账号：优先环境变量，其次本机 saves/local/dev_admin.env。"""
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


def normalize_title(text: str) -> str:
    """标题归一化：只保留小写字母与数字，用于语料标题与检索结果标题对齐。"""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())
