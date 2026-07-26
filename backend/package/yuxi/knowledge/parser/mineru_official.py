"""
MinerU Official 解析器

使用 MinerU 官方云服务 API 进行文档解析
"""

import ipaddress
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from yuxi.knowledge.parser.base import BaseDocumentProcessor, DocumentParserException
from yuxi.knowledge.parser.zip_utils import process_zip_file_sync
from yuxi.utils import hashstr, logger

MAX_MINERU_OFFICIAL_DOWNLOAD_BYTES = 512 * 1024 * 1024
MINERU_OFFICIAL_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


class MinerUOfficialParser(BaseDocumentProcessor):
    """MinerU 官方 API 解析器"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("MINERU_API_KEY")
        if not self.api_key:
            raise DocumentParserException("MINERU_API_KEY 环境变量未设置", "mineru_official", "missing_api_key")

        self.api_base = "https://mineru.net/api/v4"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        self._session = requests.Session()
        self._session.trust_env = False

    def get_service_name(self) -> str:
        return "mineru_official"

    def get_supported_extensions(self) -> list[str]:
        """MinerU 官方 API 支持的文件格式"""
        return [".pdf", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg"]

    def check_health(self) -> dict[str, Any]:
        """检查 API 可用性和密钥有效性"""
        try:
            # 查询一个不存在的批次只验证连通性和鉴权，不创建计费解析任务。
            with self._session.get(
                f"{self.api_base}/extract-results/batch/health-check",
                headers=self.headers,
                timeout=10,
                allow_redirects=False,
            ) as response:
                status_code = response.status_code

            # 如果返回 401 或特定的 API 错误码，说明密钥有问题
            if status_code == 401:
                return {"status": "unhealthy", "message": "API 密钥无效或已过期", "details": {"error_code": "A0202"}}
            if status_code == 403:
                return {"status": "unhealthy", "message": "API 密钥权限不足", "details": {"error_code": "A0211"}}
            if status_code >= 500:
                return {
                    "status": "unhealthy",
                    "message": f"API 服务异常: HTTP {status_code}",
                    "details": {"status_code": status_code},
                }
            return {
                "status": "healthy",
                "message": "MinerU 官方 API 服务可用",
                "details": {"status_code": status_code},
            }

        except requests.exceptions.Timeout:
            return {"status": "timeout", "message": "API 请求超时", "details": {"timeout": "10s"}}
        except requests.exceptions.ConnectionError:
            return {
                "status": "unavailable",
                "message": "无法连接到 MinerU 官方 API 服务",
                "details": {},
            }
        except Exception as e:
            return {
                "status": "error",
                "message": "健康检查失败",
                "details": {"error_type": type(e).__name__},
            }

    def process_file(self, file_path: str, params: dict[str, Any] | None = None) -> str:
        """
        使用 MinerU 官方 API 处理文件

        Args:
            file_path: 本地文件路径
            params: 处理参数
                - is_ocr: 是否启用 OCR (默认: True)
                - enable_formula: 是否启用公式识别 (默认: True)
                - enable_table: 是否启用表格识别 (默认: True)
                - language: 文档语言 (默认: "ch")
                - page_ranges: 页码范围 (默认: None)
                - model_version: 模型版本 "pipeline" 或 "vlm" (默认: "pipeline")

        Returns:
            str: 提取的 Markdown 文本
        """
        if not os.path.exists(file_path):
            raise DocumentParserException(f"文件不存在: {file_path}", self.get_service_name(), "file_not_found")

        file_ext = Path(file_path).suffix.lower()
        if not self.supports_file_type(file_ext):
            raise DocumentParserException(
                f"不支持的文件类型: {file_ext}", self.get_service_name(), "unsupported_file_type"
            )

        # 处理参数
        params = params or {}

        # 由于官方 API 不支持直接文件上传，我们需要先上传文件到可访问的 URL
        # 这里使用批量文件上传接口
        try:
            start_time = time.time()
            logger.info(f"MinerU Official 开始处理: {os.path.basename(file_path)}")

            # 步骤 1: 申请文件上传链接
            batch_id = self._upload_file(file_path, params)
            logger.info("文件上传成功")

            # 步骤 2: 轮询任务结果
            result = self._poll_batch_result(batch_id)
            logger.info(f"任务完成，状态: {result['state']}")

            zip_url = result.get("full_zip_url")

            zip_path = self._download_zip(zip_url)

            try:
                image_bucket = params.get("image_bucket") or "public"
                image_prefix = params.get("image_prefix") or "unknown/kb-images"

                processed = process_zip_file_sync(
                    zip_path,
                    image_bucket=image_bucket,
                    image_prefix=image_prefix,
                )
                text = processed["markdown_content"]
            except Exception as error:
                logger.error(f"MinerU Official 响应解析失败 (error_type={type(error).__name__})")
                raise DocumentParserException(
                    "MinerU Official 响应解析失败", self.get_service_name(), "response_parse_error"
                ) from error
            finally:
                try:
                    os.unlink(zip_path)
                except OSError as error:
                    logger.warning(f"MinerU Official 临时文件清理失败 (error_type={type(error).__name__})")

            if not isinstance(text, str) or not text:
                raise DocumentParserException(
                    "MinerU Official 未返回文本内容", self.get_service_name(), "no_content"
                )

            processing_time = time.time() - start_time
            logger.info(
                f"MinerU Official 处理成功: {os.path.basename(file_path)} - {len(text)} 字符 ({processing_time:.2f}s)"
            )

            return text

        except Exception as e:
            if isinstance(e, DocumentParserException):
                raise
            processing_time = time.time() - start_time
            logger.error(
                f"MinerU Official 处理失败 (error_type={type(e).__name__}, elapsed={processing_time:.2f}s)"
            )
            raise DocumentParserException(
                "MinerU Official 处理失败", self.get_service_name(), "processing_failed"
            ) from e

    def _upload_file(self, file_path: str, params: dict[str, Any]) -> str:
        """上传文件并返回 batch_id"""
        filename = os.path.basename(file_path)

        data_id = params.get("data_id", filename)
        if len(data_id) > 30:
            data_id = data_id[:30] + "_" + hashstr(data_id, length=8)

        upload_data = {
            "enable_formula": params.get("enable_formula", True),
            "enable_table": params.get("enable_table", True),
            "language": params.get("language", "ch"),
            "files": [
                {
                    "name": filename,
                    "is_ocr": params.get("is_ocr", True),
                    "data_id": data_id,
                    "page_ranges": params.get("page_ranges"),
                }
            ],
        }

        # 申请上传链接
        with self._session.post(
            f"{self.api_base}/file-urls/batch",
            headers=self.headers,
            json=upload_data,
            timeout=30,
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise DocumentParserException(
                    f"申请上传链接失败: HTTP {response.status_code}",
                    self.get_service_name(),
                    "upload_url_failed",
                )
            try:
                result = response.json()
            except ValueError as error:
                raise DocumentParserException(
                    "申请上传链接响应格式无效", self.get_service_name(), "response_parse_error"
                ) from error

        if not isinstance(result, dict):
            raise DocumentParserException(
                "申请上传链接响应格式无效", self.get_service_name(), "response_parse_error"
            )
        if result.get("code") != 0:
            raise DocumentParserException(
                "申请上传链接失败",
                self.get_service_name(),
                f"api_error_{result.get('code', 'unknown')}",
            )

        result_data = result.get("data")
        if not isinstance(result_data, dict):
            raise DocumentParserException(
                "申请上传链接响应格式无效", self.get_service_name(), "response_parse_error"
            )
        batch_id = result_data.get("batch_id")
        upload_urls = result_data.get("file_urls")

        if not isinstance(batch_id, str) or not isinstance(upload_urls, list) or not upload_urls:
            raise DocumentParserException("未获取到文件上传链接", self.get_service_name(), "no_upload_url")

        # 上传文件
        upload_url = upload_urls[0]
        self._validate_provider_url(upload_url)
        with open(file_path, "rb") as f:
            with self._session.put(upload_url, data=f, timeout=60, allow_redirects=False) as upload_response:
                if upload_response.status_code != 200:
                    raise DocumentParserException(
                        f"文件上传失败: HTTP {upload_response.status_code}",
                        self.get_service_name(),
                        "file_upload_failed",
                    )

        return batch_id

    def _poll_batch_result(self, batch_id: str, max_wait_time: int = 600) -> dict[str, Any]:
        """轮询批量任务结果"""
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            with self._session.get(
                f"{self.api_base}/extract-results/batch/{batch_id}",
                headers=self.headers,
                timeout=30,
                allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise DocumentParserException(
                        f"查询任务状态失败: HTTP {response.status_code}",
                        self.get_service_name(),
                        "status_query_failed",
                    )
                try:
                    result = response.json()
                except ValueError as error:
                    raise DocumentParserException(
                        "查询任务状态响应格式无效", self.get_service_name(), "response_parse_error"
                    ) from error

            if not isinstance(result, dict):
                raise DocumentParserException(
                    "查询任务状态响应格式无效", self.get_service_name(), "response_parse_error"
                )
            if result.get("code") != 0:
                raise DocumentParserException(
                    "查询任务状态失败",
                    self.get_service_name(),
                    f"api_error_{result.get('code', 'unknown')}",
                )

            result_data = result.get("data")
            if not isinstance(result_data, dict):
                raise DocumentParserException(
                    "查询任务状态响应格式无效", self.get_service_name(), "response_parse_error"
                )
            extract_results = result_data.get("extract_result", [])
            if not extract_results:
                time.sleep(5)
                continue
            if not isinstance(extract_results, list) or not isinstance(extract_results[0], dict):
                raise DocumentParserException(
                    "查询任务状态响应格式无效", self.get_service_name(), "response_parse_error"
                )

            # 检查第一个文件的状态
            file_result = extract_results[0]
            state = file_result.get("state")

            if state == "done":
                return file_result
            elif state == "failed":
                raise DocumentParserException("文档解析失败", self.get_service_name(), "parsing_failed")

            # 继续等待
            time.sleep(5)

        raise DocumentParserException("任务处理超时", self.get_service_name(), "timeout")

    def _download_zip(self, zip_url: str) -> str:
        """下载结果ZIP到临时文件并返回路径"""
        if not zip_url:
            raise DocumentParserException("未获取到结果下载链接", self.get_service_name(), "no_download_url")
        self._validate_provider_url(zip_url)
        temp_path: str | None = None
        try:
            with self._session.get(zip_url, timeout=60, stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise DocumentParserException(
                        f"下载结果失败: HTTP {response.status_code}", self.get_service_name(), "download_failed"
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as error:
                        raise DocumentParserException(
                            "下载结果大小无效", self.get_service_name(), "download_failed"
                        ) from error
                    if declared_size < 0 or declared_size > MAX_MINERU_OFFICIAL_DOWNLOAD_BYTES:
                        raise DocumentParserException(
                            "下载结果超过大小限制", self.get_service_name(), "download_too_large"
                        )

                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                    temp_path = tmp_file.name
                    downloaded_size = 0
                    for chunk in response.iter_content(chunk_size=MINERU_OFFICIAL_DOWNLOAD_CHUNK_SIZE):
                        if not chunk:
                            continue
                        downloaded_size += len(chunk)
                        if downloaded_size > MAX_MINERU_OFFICIAL_DOWNLOAD_BYTES:
                            raise DocumentParserException(
                                "下载结果超过大小限制", self.get_service_name(), "download_too_large"
                            )
                        tmp_file.write(chunk)
            return temp_path
        except Exception:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    @staticmethod
    def _validate_provider_url(url: Any) -> None:
        if not isinstance(url, str):
            raise DocumentParserException("供应商返回的 URL 无效", "mineru_official", "invalid_url")
        try:
            parsed = urlparse(url)
        except ValueError as error:
            raise DocumentParserException(
                "供应商返回的 URL 无效", "mineru_official", "invalid_url"
            ) from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise DocumentParserException("供应商返回的 URL 无效", "mineru_official", "invalid_url")
        try:
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        except OSError as error:
            raise DocumentParserException(
                "供应商返回的 URL 无法解析", "mineru_official", "invalid_url"
            ) from error
        has_non_public_address = any(
            not ipaddress.ip_address(address[4][0].split("%", 1)[0]).is_global for address in addresses
        )
        if not addresses or has_non_public_address:
            raise DocumentParserException("供应商返回的 URL 无效", "mineru_official", "invalid_url")

    def close(self) -> None:
        self._session.close()
