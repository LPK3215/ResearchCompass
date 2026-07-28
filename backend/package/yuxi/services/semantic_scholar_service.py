"""ResearchCompass Semantic Scholar 外部学术数据源客户端。

本模块是本仓库作者为科研场景接入的外部学术数据源客户端：封装 Semantic Scholar
Graph API 的论文检索/解析、引用/参考文献边拉取与开放获取 PDF 下载，并对重试、
限流、私有网络防 SSRF、PDF 签名校验等外部输入风险做显式处理。本模块不依赖 Yuxi
的智能体运行时，仅作为科研业务（论文导入、引用图谱同步）的外部数据适配层。
"""

from __future__ import annotations

import asyncio
import ipaddress
import math
import os
import re
import socket
import time
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx


SEMANTIC_SCHOLAR_FIELDS = (
    "paperId,externalIds,title,abstract,year,venue,citationCount,referenceCount,"
    "influentialCitationCount,isOpenAccess,openAccessPdf,authors,fieldsOfStudy,s2FieldsOfStudy"
)
SEMANTIC_SCHOLAR_EDGE_PAPER_FIELDS = ",".join(
    f"{{paper_field}}.{field}" for field in SEMANTIC_SCHOLAR_FIELDS.split(",")
)
MAX_OPEN_ACCESS_PDF_BYTES = 100 * 1024 * 1024
MAX_SEMANTIC_SCHOLAR_RETRY_SECONDS = 60.0
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_IDENTIFIER_PREFIX_PATTERN = re.compile(r"^(?:doi|arxiv|corpusid|pmid|pmcid|acl|s2paperid):", re.IGNORECASE)


def _is_public_ip_address(value: Any) -> bool:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    return address.is_global


async def _is_private_or_unresolvable_host(hostname: str) -> bool:
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except OSError:
        return True
    for address in addresses:
        if not _is_public_ip_address(address[4][0]):
            return True
    return False


def _response_has_public_peer(response: httpx.Response) -> bool:
    network_stream = response.extensions.get("network_stream")
    if network_stream is None:
        return False
    try:
        server_address = network_stream.get_extra_info("server_addr")
    except Exception:
        return False
    if isinstance(server_address, (tuple, list)) and server_address:
        server_address = server_address[0]
    return _is_public_ip_address(server_address)


def _retry_wait_seconds(value: str | None, fallback: float) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(parsed) or parsed <= 0:
        return fallback
    return min(parsed, MAX_SEMANTIC_SCHOLAR_RETRY_SECONDS)


class SemanticScholarError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def normalize_paper_title(value: str) -> str:
    return " ".join(re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.casefold()).split())


def is_paper_identifier(value: str) -> bool:
    normalized = str(value or "").strip()
    return bool(
        _DOI_PATTERN.fullmatch(normalized)
        or _IDENTIFIER_PREFIX_PATTERN.match(normalized)
        or re.fullmatch(r"[0-9a-f]{40}", normalized, re.IGNORECASE)
    )


def normalize_paper_identifier(value: str) -> str:
    normalized = str(value or "").strip()
    if _DOI_PATTERN.fullmatch(normalized):
        return f"DOI:{normalized}"
    if normalized.casefold().startswith("s2paperid:"):
        return normalized.split(":", 1)[1].strip()
    return normalized


class SemanticScholarClient:
    # Semantic Scholar API 限流：免费 Key 约 1 请求/秒，主动间隔避免触发 429
    _MIN_REQUEST_INTERVAL = 1.2

    def __init__(self) -> None:
        self.base_url = os.getenv("SEMANTIC_SCHOLAR_API_BASE", "https://api.semanticscholar.org/graph/v1").rstrip("/")
        api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        self.headers = {"User-Agent": "ResearchCompass/0.11"}
        if api_key:
            self.headers["x-api-key"] = api_key
        self.timeout = httpx.Timeout(45.0, connect=10.0)
        self._last_request_time = 0.0

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        max_retries = 3
        backoff_seconds = [10, 30, 60]
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=self.headers,
            trust_env=False,
        ) as client:
            for attempt in range(max_retries + 1):
                if attempt == 0:
                    elapsed = time.monotonic() - self._last_request_time
                    if elapsed < self._MIN_REQUEST_INTERVAL:
                        await asyncio.sleep(self._MIN_REQUEST_INTERVAL - elapsed)
                    self._last_request_time = time.monotonic()
                try:
                    response = await client.get(f"{self.base_url}{path}", params=params)
                except httpx.TimeoutException as exc:
                    raise SemanticScholarError("semantic_scholar_timeout", "Semantic Scholar 请求超时") from exc
                except httpx.HTTPError as exc:
                    raise SemanticScholarError("semantic_scholar_network", "Semantic Scholar 网络请求失败") from exc
                if response.status_code == 429 and attempt < max_retries:
                    wait = _retry_wait_seconds(response.headers.get("Retry-After"), backoff_seconds[attempt])
                    await asyncio.sleep(wait)
                    continue
                if response.status_code == 429:
                    raise SemanticScholarError(
                        "semantic_scholar_rate_limited",
                        "Semantic Scholar API 触发限流，已重试 3 次仍失败",
                    )
                break
        if response.status_code == 404:
            raise SemanticScholarError("paper_not_found", "Semantic Scholar 未找到论文")
        if response.status_code >= 400:
            raise SemanticScholarError(
                "semantic_scholar_http_error",
                f"Semantic Scholar API 返回 HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SemanticScholarError(
                "semantic_scholar_invalid_response", "Semantic Scholar 返回非 JSON 响应"
            ) from exc
        if not isinstance(payload, dict):
            raise SemanticScholarError("semantic_scholar_invalid_response", "Semantic Scholar 响应必须是对象")
        return payload

    async def resolve_paper(self, local_paper: Any) -> dict[str, Any]:
        external_ids = local_paper.external_ids or {}
        identifiers = [
            f"DOI:{local_paper.doi}" if local_paper.doi else None,
            external_ids.get("SemanticScholar") or external_ids.get("S2PaperId"),
            f"ARXIV:{external_ids['ArXiv']}" if external_ids.get("ArXiv") else None,
            f"CorpusId:{external_ids['CorpusId']}" if external_ids.get("CorpusId") else None,
        ]
        for identifier in identifiers:
            identifier = str(identifier).strip() if identifier else ""
            if not identifier:
                continue
            try:
                paper = await self._get(
                    f"/paper/{quote(identifier, safe=':')}", {"fields": SEMANTIC_SCHOLAR_FIELDS}
                )
                self._validate_match(local_paper, paper)
                return paper
            except SemanticScholarError as exc:
                if exc.error_type != "paper_not_found":
                    raise

        payload = await self._get(
            "/paper/search",
            {"query": local_paper.title, "limit": 10, "fields": SEMANTIC_SCHOLAR_FIELDS},
        )
        candidates = payload.get("data")
        if not isinstance(candidates, list):
            raise SemanticScholarError("semantic_scholar_invalid_response", "论文搜索响应缺少 data 数组")
        normalized_title = normalize_paper_title(local_paper.title)
        matches = [
            paper
            for paper in candidates
            if isinstance(paper, dict)
            and normalize_paper_title(str(paper.get("title") or "")) == normalized_title
            and (
                local_paper.publication_year is None
                or paper.get("year") is None
                or int(paper["year"]) == int(local_paper.publication_year)
            )
        ]
        if not matches:
            raise SemanticScholarError("paper_match_not_found", "未找到标题和年份严格匹配的 Semantic Scholar 论文")
        if len(matches) > 1:
            raise SemanticScholarError("paper_match_ambiguous", "Semantic Scholar 返回多个标题和年份相同的候选论文")
        self._validate_match(local_paper, matches[0])
        return matches[0]

    async def get_paper(self, identifier: str) -> dict[str, Any]:
        normalized = normalize_paper_identifier(identifier)
        if not normalized:
            raise SemanticScholarError("invalid_paper_identifier", "Semantic Scholar 论文标识不能为空")
        return await self._get(
            f"/paper/{quote(normalized, safe=':')}",
            {"fields": SEMANTIC_SCHOLAR_FIELDS},
        )

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise SemanticScholarError("invalid_search_query", "论文搜索关键词不能为空")
        payload = await self._get(
            "/paper/search",
            {
                "query": normalized_query,
                "limit": min(max(int(limit), 1), 20),
                "fields": SEMANTIC_SCHOLAR_FIELDS,
            },
        )
        candidates = payload.get("data")
        if not isinstance(candidates, list):
            raise SemanticScholarError("semantic_scholar_invalid_response", "论文搜索响应缺少 data 数组")
        return [item for item in candidates if isinstance(item, dict)]

    async def download_open_access_pdf(
        self, url: str, *, max_size: int = MAX_OPEN_ACCESS_PDF_BYTES
    ) -> tuple[bytes, str]:
        """下载 Semantic Scholar 标注的公开 PDF。

        由于下载地址来自外部输入，需显式校验重定向目标与私有网络地址，并要求响应
        体内含真实 PDF 签名，避免被 HTML 错误页或内网地址误导。
        """
        current_url = str(url or "").strip()
        if not current_url:
            raise SemanticScholarError("paper_pdf_unavailable", "论文没有公开 PDF 下载地址")

        redirect_count = 0
        timeout = httpx.Timeout(60.0, connect=15.0)
        headers = {"User-Agent": "ResearchCompass/1.0 academic-paper-import"}
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                headers=headers,
                trust_env=False,
            ) as client:
                while True:
                    try:
                        parsed = urlparse(current_url)
                        hostname = parsed.hostname
                    except ValueError as exc:
                        raise SemanticScholarError(
                            "paper_pdf_invalid_url", "公开 PDF 地址不是安全的 HTTP(S) 地址"
                        ) from exc
                    if (
                        parsed.scheme not in {"http", "https"}
                        or not hostname
                        or parsed.username is not None
                        or parsed.password is not None
                    ):
                        raise SemanticScholarError("paper_pdf_invalid_url", "公开 PDF 地址不是安全的 HTTP(S) 地址")
                    if await _is_private_or_unresolvable_host(hostname):
                        raise SemanticScholarError("paper_pdf_private_network", "公开 PDF 地址指向私有网络")

                    async with client.stream("GET", current_url) as response:
                        if not _response_has_public_peer(response):
                            raise SemanticScholarError(
                                "paper_pdf_private_network",
                                "公开 PDF 下载连接未落到可验证的公网地址",
                            )
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if redirect_count >= 5:
                                raise SemanticScholarError("paper_pdf_redirect_loop", "公开 PDF 重定向次数过多")
                            location = response.headers.get("location")
                            if not location:
                                raise SemanticScholarError("paper_pdf_invalid_response", "公开 PDF 重定向缺少目标地址")
                            current_url = urljoin(current_url, location)
                            redirect_count += 1
                            continue
                        if response.status_code >= 400:
                            raise SemanticScholarError(
                                "paper_pdf_download_failed",
                                f"公开 PDF 下载返回 HTTP {response.status_code}",
                            )

                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                content_length_value = int(content_length)
                            except ValueError as exc:
                                raise SemanticScholarError("paper_pdf_invalid_response", "公开 PDF 长度头无效") from exc
                            if content_length_value > max_size:
                                raise SemanticScholarError("paper_pdf_too_large", "公开 PDF 超过 100 MB 大小限制")
                        content_type = response.headers.get("content-type", "").lower()
                        if (
                            content_type
                            and not any(
                                value in content_type for value in ("application/pdf", "application/octet-stream")
                            )
                            and not current_url.lower().split("?", 1)[0].endswith(".pdf")
                        ):
                            raise SemanticScholarError("paper_pdf_invalid_type", "公开地址未返回 PDF 文件")

                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > max_size:
                                raise SemanticScholarError("paper_pdf_too_large", "公开 PDF 超过 100 MB 大小限制")
                        if not content.startswith(b"%PDF-"):
                            raise SemanticScholarError("paper_pdf_invalid_content", "下载内容不是有效 PDF")
                        return bytes(content), current_url
        except SemanticScholarError:
            raise
        except httpx.TimeoutException as exc:
            raise SemanticScholarError("paper_pdf_timeout", "公开 PDF 下载超时") from exc
        except httpx.HTTPError as exc:
            raise SemanticScholarError("paper_pdf_download_failed", "公开 PDF 网络下载失败") from exc

    @staticmethod
    def _validate_match(local_paper: Any, remote: dict[str, Any]) -> None:
        if not remote.get("paperId") or not remote.get("title"):
            raise SemanticScholarError("semantic_scholar_invalid_response", "远端论文缺少 paperId 或 title")
        local_title = normalize_paper_title(local_paper.title)
        remote_title = normalize_paper_title(str(remote["title"]))
        if local_title != remote_title:
            raise SemanticScholarError("paper_title_conflict", "本地与 Semantic Scholar 论文标题不一致")
        if (
            local_paper.publication_year is not None
            and remote.get("year") is not None
            and int(local_paper.publication_year) != int(remote["year"])
        ):
            raise SemanticScholarError("paper_year_conflict", "本地与 Semantic Scholar 发表年份不一致")

    async def list_citations(self, paper_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._list_edges(paper_id, "citations", "citingPaper", limit)

    async def list_references(self, paper_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._list_edges(paper_id, "references", "citedPaper", limit)

    async def _list_edges(self, paper_id: str, endpoint: str, paper_field: str, limit: int) -> list[dict[str, Any]]:
        capped_limit = min(max(int(limit), 0), 5000)
        remaining = capped_limit
        offset = 0
        edges: list[dict[str, Any]] = []
        fields = f"contexts,intents,isInfluential,{SEMANTIC_SCHOLAR_EDGE_PAPER_FIELDS.format(paper_field=paper_field)}"
        while remaining > 0:
            page_size = min(remaining, 1000)
            payload = await self._get(
                f"/paper/{quote(paper_id, safe='')}/{endpoint}",
                {"offset": offset, "limit": page_size, "fields": fields},
            )
            page = self._validate_edges(payload)
            edges.extend(page)
            if len(page) < page_size or payload.get("next") is None:
                break
            offset = int(payload["next"])
            remaining = capped_limit - len(edges)
        return edges[:capped_limit]

    @staticmethod
    def _validate_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise SemanticScholarError("semantic_scholar_invalid_response", "引用响应缺少 data 数组")
        return [item for item in data if isinstance(item, dict)]


__all__ = [
    "SemanticScholarClient",
    "SemanticScholarError",
    "is_paper_identifier",
    "normalize_paper_identifier",
    "normalize_paper_title",
]
