"""PaddleOCR API jobs parser."""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from yuxi.knowledge.parser.base import BaseDocumentProcessor, DocumentParserException
from yuxi.storage.minio import get_minio_client
from yuxi.utils import logger

DEFAULT_PADDLEOCR_API_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
PADDLEOCR_SUPPORTED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"]
MAX_PADDLEOCR_RESULT_BYTES = 50 * 1024 * 1024
MAX_PADDLEOCR_IMAGE_BYTES = 25 * 1024 * 1024
PADDLEOCR_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


class PaddleOCRAPIParser(BaseDocumentProcessor):
    """Base parser for PaddleOCR cloud jobs API."""

    model = ""
    service_name = ""
    default_optional_payload: dict[str, bool] = {}

    def __init__(self, api_token: str | None = None, api_url: str | None = None):
        self.api_token = api_token or os.getenv("PADDLEOCR_API_TOKEN")
        self.api_url = (api_url or os.getenv("PADDLEOCR_API_URL") or DEFAULT_PADDLEOCR_API_URL).rstrip("/")
        self._session = requests.Session()
        self._session.trust_env = False

    def get_service_name(self) -> str:
        return self.service_name

    def get_supported_extensions(self) -> list[str]:
        return PADDLEOCR_SUPPORTED_EXTENSIONS

    def check_health(self) -> dict[str, Any]:
        if not self.api_token:
            return {
                "status": "unavailable",
                "message": "PADDLEOCR_API_TOKEN 未配置",
                "details": {"model": self.model},
            }

        return {
            "status": "configured",
            "message": "PaddleOCR API token 已配置，将在解析时验证",
            "details": {"model": self.model},
        }

    def process_file(self, file_path: str, params: dict[str, Any] | None = None) -> str:
        if not os.path.exists(file_path) and not file_path.startswith(("http://", "https://")):
            raise DocumentParserException(f"文件不存在: {file_path}", self.get_service_name(), "file_not_found")

        file_ext = self._file_extension(file_path)
        if file_ext and not self.supports_file_type(file_ext):
            raise DocumentParserException(
                f"不支持的文件类型: {file_ext}", self.get_service_name(), "unsupported_file_type"
            )
        if file_path.startswith(("http://", "https://")):
            self._validate_public_https_url(file_path)

        self._require_api_token()
        params = params or {}
        start_time = time.time()

        try:
            logger.info(f"PaddleOCR API 开始处理 ({self.model})")
            job_id = self._submit_job(file_path, params)
            result_url = self._poll_job_result(
                job_id=job_id,
                poll_interval_seconds=float(params.get("poll_interval_seconds") or 5),
                max_wait_seconds=float(params.get("max_wait_seconds") or 600),
            )
            rows = self._download_jsonl(result_url)
            text = self._extract_markdown(rows, params)

            processing_time = time.time() - start_time
            logger.info(
                f"PaddleOCR API 处理成功: {Path(file_path).name} ({self.model}) - "
                f"{len(text)} 字符 ({processing_time:.2f}s)"
            )
            return text
        except DocumentParserException:
            raise
        except Exception as exc:
            processing_time = time.time() - start_time
            logger.error(
                f"PaddleOCR API 处理失败 (error_type={type(exc).__name__}, elapsed={processing_time:.2f}s)"
            )
            raise DocumentParserException(
                "PaddleOCR API 处理失败", self.get_service_name(), "processing_failed"
            ) from exc

    def _require_api_token(self) -> None:
        if not self.api_token:
            raise DocumentParserException("PADDLEOCR_API_TOKEN 未配置", self.get_service_name(), "missing_api_token")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self.api_token}"}

    def _file_extension(self, file_path: str) -> str:
        if file_path.startswith(("http://", "https://")):
            return Path(urlparse(file_path).path).suffix.lower()
        return Path(file_path).suffix.lower()

    def _resolve_optional_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = dict(self.default_optional_payload)
        overrides = params.get("optional_payload")
        if isinstance(overrides, dict):
            for key in payload:
                if key in overrides:
                    payload[key] = overrides[key]
        return payload

    def _submit_job(self, file_path: str, params: dict[str, Any]) -> str:
        optional_payload = self._resolve_optional_payload(params)
        headers = self._headers()

        if file_path.startswith(("http://", "https://")):
            response = self._session.post(
                self.api_url,
                headers={**headers, "Content-Type": "application/json"},
                json={"fileUrl": file_path, "model": self.model, "optionalPayload": optional_payload},
                timeout=60,
                allow_redirects=False,
            )
        else:
            with open(file_path, "rb") as file:
                response = self._session.post(
                    self.api_url,
                    headers=headers,
                    data={"model": self.model, "optionalPayload": json.dumps(optional_payload, ensure_ascii=False)},
                    files={"file": file},
                    timeout=60,
                    allow_redirects=False,
                )

        try:
            if response.status_code != 200:
                raise DocumentParserException(
                    f"提交 PaddleOCR 任务失败: HTTP {response.status_code}",
                    self.get_service_name(),
                    "submit_failed",
                )
            try:
                body = response.json()
            except ValueError as error:
                raise DocumentParserException(
                    "提交 PaddleOCR 任务响应格式无效", self.get_service_name(), "response_parse_error"
                ) from error
        finally:
            response.close()

        if not isinstance(body, dict):
            raise DocumentParserException(
                "提交 PaddleOCR 任务响应格式无效", self.get_service_name(), "response_parse_error"
            )
        if body.get("code") not in (None, 0):
            raise DocumentParserException(
                "提交 PaddleOCR 任务失败",
                self.get_service_name(),
                f"api_error_{body.get('code')}",
            )

        body_data = body.get("data")
        job_id = body_data.get("jobId") if isinstance(body_data, dict) else None
        if not isinstance(job_id, (str, int)) or not str(job_id).strip():
            raise DocumentParserException(
                "提交 PaddleOCR 任务后未返回 jobId",
                self.get_service_name(),
                "missing_job_id",
            )

        return str(job_id)

    def _poll_job_result(self, job_id: str, poll_interval_seconds: float, max_wait_seconds: float) -> str:
        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            response = self._session.get(
                f"{self.api_url}/{job_id}",
                headers=self._headers(),
                timeout=30,
                allow_redirects=False,
            )
            try:
                if response.status_code != 200:
                    raise DocumentParserException(
                        f"查询 PaddleOCR 任务失败: HTTP {response.status_code}",
                        self.get_service_name(),
                        "status_query_failed",
                    )
                try:
                    body = response.json()
                except ValueError as error:
                    raise DocumentParserException(
                        "查询 PaddleOCR 任务响应格式无效", self.get_service_name(), "response_parse_error"
                    ) from error
            finally:
                response.close()

            if not isinstance(body, dict):
                raise DocumentParserException(
                    "查询 PaddleOCR 任务响应格式无效", self.get_service_name(), "response_parse_error"
                )
            data = body.get("data")
            if not isinstance(data, dict):
                raise DocumentParserException(
                    "查询 PaddleOCR 任务响应格式无效", self.get_service_name(), "response_parse_error"
                )
            state = data.get("state")
            if state == "done":
                result_url = data.get("resultUrl")
                json_url = (result_url.get("jsonUrl") or "").strip() if isinstance(result_url, dict) else ""
                if not json_url:
                    raise DocumentParserException(
                        "PaddleOCR 任务完成但未返回 jsonUrl", self.get_service_name(), "missing_result_url"
                    )
                self._validate_public_https_url(json_url)
                return json_url

            if state == "failed":
                raise DocumentParserException("PaddleOCR 任务失败", self.get_service_name(), "job_failed")

            if state not in {"pending", "running"}:
                raise DocumentParserException(
                    "PaddleOCR 任务状态异常", self.get_service_name(), "unknown_job_state"
                )

            time.sleep(poll_interval_seconds)

        raise DocumentParserException("PaddleOCR 任务处理超时", self.get_service_name(), "timeout")

    def _download_jsonl(self, json_url: str) -> list[dict[str, Any]]:
        self._validate_public_https_url(json_url)
        response = self._session.get(json_url, timeout=60, stream=True, allow_redirects=False)
        rows: list[dict[str, Any]] = []
        downloaded_size = 0
        try:
            if response.status_code != 200:
                raise DocumentParserException(
                    f"下载 PaddleOCR 结果失败: HTTP {response.status_code}",
                    self.get_service_name(),
                    "download_failed",
                )
            self._validate_content_length(response, MAX_PADDLEOCR_RESULT_BYTES, "PaddleOCR 结果")
            for raw_line in response.iter_lines(decode_unicode=False):
                downloaded_size += len(raw_line) + 1
                if downloaded_size > MAX_PADDLEOCR_RESULT_BYTES:
                    raise DocumentParserException(
                        "PaddleOCR 结果超过大小限制", self.get_service_name(), "download_too_large"
                    )
                if not raw_line.strip():
                    continue
                try:
                    item = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise DocumentParserException(
                        "PaddleOCR 结果格式无效", self.get_service_name(), "response_parse_error"
                    ) from error
                if not isinstance(item, dict):
                    raise DocumentParserException(
                        "PaddleOCR 结果格式无效", self.get_service_name(), "response_parse_error"
                    )
                rows.append(item)
        finally:
            response.close()

        if not rows:
            raise DocumentParserException("PaddleOCR 结果为空", self.get_service_name(), "empty_result")

        return rows

    def _extract_markdown(self, rows: list[dict[str, Any]], params: dict[str, Any]) -> str:
        raise NotImplementedError

    def _upload_markdown_image(self, image_url: str, image_path: str, params: dict[str, Any]) -> str:
        self._validate_public_https_url(image_url)
        response = self._session.get(image_url, timeout=60, stream=True, allow_redirects=False)
        try:
            if response.status_code != 200:
                raise DocumentParserException(
                    f"下载 PaddleOCR Markdown 图片失败: HTTP {response.status_code}",
                    self.get_service_name(),
                    "image_download_failed",
                )
            self._validate_content_length(response, MAX_PADDLEOCR_IMAGE_BYTES, "PaddleOCR 图片")
            response_headers = response.headers or {}
            content_type = (response_headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if not content_type.startswith("image/"):
                raise DocumentParserException(
                    "PaddleOCR Markdown 图片类型无效", self.get_service_name(), "image_download_failed"
                )
            image_data = bytearray()
            for chunk in response.iter_content(chunk_size=PADDLEOCR_DOWNLOAD_CHUNK_SIZE):
                if not chunk:
                    continue
                image_data.extend(chunk)
                if len(image_data) > MAX_PADDLEOCR_IMAGE_BYTES:
                    raise DocumentParserException(
                        "PaddleOCR 图片超过大小限制", self.get_service_name(), "download_too_large"
                    )
        finally:
            response.close()

        image_bucket = params.get("image_bucket") or "public"
        image_prefix = str(params.get("image_prefix") or "unknown/kb-images").strip("/") or "unknown/kb-images"
        filename = Path(image_path).name or "paddleocr_image"
        suffix = Path(filename).suffix
        if not suffix:
            suffix = mimetypes.guess_extension(content_type) or ".jpg"
            filename = f"{filename}{suffix}"

        object_name = f"{image_prefix}/{int(time.time() * 1000000)}_{filename}"
        minio_client = get_minio_client()
        minio_client.ensure_bucket_exists(image_bucket)
        upload_result = minio_client.upload_file(
            bucket_name=image_bucket,
            object_name=object_name,
            data=bytes(image_data),
        )
        return upload_result.url

    def _validate_content_length(self, response: Any, max_size: int, label: str) -> None:
        content_length = (response.headers or {}).get("content-length")
        if content_length is None:
            return
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise DocumentParserException(
                f"{label}大小无效", self.get_service_name(), "download_failed"
            ) from error
        if declared_size < 0 or declared_size > max_size:
            raise DocumentParserException(f"{label}超过大小限制", self.get_service_name(), "download_too_large")

    @staticmethod
    def _validate_public_https_url(url: str) -> None:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError as error:
            raise DocumentParserException("PaddleOCR URL 无效", "paddleocr_api", "invalid_url") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise DocumentParserException("PaddleOCR URL 无效", "paddleocr_api", "invalid_url")
        try:
            addresses = socket.getaddrinfo(parsed.hostname, port or 443)
        except OSError as error:
            raise DocumentParserException("PaddleOCR URL 无法解析", "paddleocr_api", "invalid_url") from error
        if not addresses or any(
            not ipaddress.ip_address(address[4][0].split("%", 1)[0]).is_global for address in addresses
        ):
            raise DocumentParserException("PaddleOCR URL 无效", "paddleocr_api", "invalid_url")

    def close(self) -> None:
        self._session.close()


class PaddleOCRVLParser(PaddleOCRAPIParser):
    """PaddleOCR-VL parser that returns layout Markdown."""

    model = "PaddleOCR-VL-1.6"
    service_name = "paddleocr_vl_1_6"
    default_optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }

    def _extract_markdown(self, rows: list[dict[str, Any]], params: dict[str, Any]) -> str:
        markdown_parts: list[str] = []

        for row in rows:
            result = row.get("result") or {}
            for item in result.get("layoutParsingResults") or []:
                markdown = item.get("markdown") or {}
                text = markdown.get("text")
                if not isinstance(text, str):
                    continue

                for image_path, image_url in (markdown.get("images") or {}).items():
                    if not image_path or not image_url:
                        continue
                    uploaded_url = self._upload_markdown_image(str(image_url), str(image_path), params)
                    text = text.replace(f"]({image_path})", f"]({uploaded_url})")
                    text = text.replace(str(image_url), uploaded_url)

                if text.strip():
                    markdown_parts.append(text.strip())

        return "\n\n".join(markdown_parts).strip()


class PaddleOCRPPOCRv6Parser(PaddleOCRAPIParser):
    """PP-OCRv6 parser that returns plain OCR text."""

    model = "PP-OCRv6"
    service_name = "paddleocr_pp_ocrv6"
    default_optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useTextlineOrientation": False,
    }

    def _extract_markdown(self, rows: list[dict[str, Any]], params: dict[str, Any]) -> str:
        lines: list[str] = []

        for row in rows:
            result = row.get("result") or {}
            for item in result.get("ocrResults") or []:
                pruned_result = item.get("prunedResult") or {}
                rec_texts = pruned_result.get("rec_texts") or []
                for text in rec_texts:
                    if isinstance(text, str) and text.strip():
                        lines.append(text.strip())

        return "\n".join(lines).strip()
