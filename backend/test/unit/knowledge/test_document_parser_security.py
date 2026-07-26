from __future__ import annotations

import os
from pathlib import Path

import pytest

from yuxi.knowledge.parser import factory as factory_module
from yuxi.knowledge.parser.base import DocumentParserException, OCRException
from yuxi.knowledge.parser.deepseek_ocr import DeepSeekOCRParser
from yuxi.knowledge.parser.factory import DocumentProcessorFactory
from yuxi.knowledge.parser.mineru import MinerUParser
from yuxi.knowledge.parser.mineru_official import MinerUOfficialParser
from yuxi.knowledge.parser.pp_structure_v3 import PPStructureV3Parser
from yuxi.knowledge.parser.rapid_ocr import RapidOCRParser

SECRET = "Authorization=secret-api-key provider-body=<private>"


class CapturedLogger:
    def __init__(self):
        self.messages: list[str] = []

    def _capture(self, message: str) -> None:
        self.messages.append(message)

    info = _capture
    warning = _capture
    error = _capture
    debug = _capture


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: object | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ):
        self.status_code = status_code
        self.payload = {} if payload is None else payload
        self.text = text
        self.headers = headers or {}
        self.chunks = chunks or []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.closed = True
        return False

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield from self.chunks


def test_factory_cache_key_hashes_sensitive_kwargs_and_clear_closes_instances(monkeypatch):
    cache: dict = {}
    monkeypatch.setattr(factory_module, "_PROCESSOR_CACHE", cache)

    class FakeProcessor:
        closed = False

        def close(self) -> None:
            self.closed = True

    processor = FakeProcessor()
    cache[DocumentProcessorFactory._build_cache_key("deepseek_ocr", {"api_key": SECRET})] = processor

    assert SECRET not in next(iter(cache))

    DocumentProcessorFactory.clear_cache()

    assert processor.closed is True
    assert cache == {}


def test_factory_health_failure_is_sanitized(monkeypatch):
    monkeypatch.setattr(
        DocumentProcessorFactory,
        "get_processor",
        lambda processor_type: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )

    result = DocumentProcessorFactory.check_health("deepseek_ocr")

    assert result["message"] == "健康检查失败"
    assert result["details"] == {"error_type": "RuntimeError"}
    assert SECRET not in str(result)


def test_deepseek_api_error_does_not_expose_provider_body(monkeypatch):
    captured_logger = CapturedLogger()
    response = FakeResponse(status_code=502, text=SECRET)

    class FakeSession:
        trust_env = False

        def post(self, *args, **kwargs):
            return response

        def close(self) -> None:
            return None

    parser = DeepSeekOCRParser(api_key="test-key")
    parser._session.close()
    parser._session = FakeSession()
    monkeypatch.setattr("yuxi.knowledge.parser.deepseek_ocr.logger", captured_logger)

    with pytest.raises(DocumentParserException) as exc_info:
        parser._call_api(b"image", "image/png")

    assert response.closed is True
    assert SECRET not in str(exc_info.value)
    assert SECRET not in " ".join(captured_logger.messages)


def test_mineru_streams_response_and_removes_temporary_zip(monkeypatch, tmp_path: Path):
    response = FakeResponse(
        status_code=200,
        headers={"content-length": "8", "content-type": "application/zip"},
        chunks=[b"zip-", b"data"],
    )
    post_kwargs: dict = {}
    processed_paths: list[str] = []

    class FakeSession:
        trust_env = False

        def post(self, *args, **kwargs):
            post_kwargs.update(kwargs)
            return response

        def close(self) -> None:
            return None

    def fake_process_zip(path: str, **kwargs):
        processed_paths.append(path)
        assert Path(path).read_bytes() == b"zip-data"
        return {"markdown_content": "parsed"}

    file_path = tmp_path / "document.pdf"
    file_path.write_bytes(b"pdf")
    parser = MinerUParser(server_url="http://mineru.test")
    parser._session.close()
    parser._session = FakeSession()
    monkeypatch.setattr("yuxi.knowledge.parser.mineru.process_zip_file_sync", fake_process_zip)

    assert parser.process_file(str(file_path)) == "parsed"
    assert post_kwargs["stream"] is True
    assert response.closed is True
    assert processed_paths and not os.path.exists(processed_paths[0])


def test_mineru_official_health_check_does_not_create_task():
    requested_urls: list[str] = []

    class FakeSession:
        trust_env = False

        def get(self, url: str, **kwargs):
            requested_urls.append(url)
            return FakeResponse(status_code=404)

        def post(self, *args, **kwargs):
            raise AssertionError("health check must not create a parsing task")

        def close(self) -> None:
            return None

    parser = MinerUOfficialParser(api_key="test-key")
    parser._session.close()
    parser._session = FakeSession()

    result = parser.check_health()

    assert result["status"] == "healthy"
    assert requested_urls == [f"{parser.api_base}/extract-results/batch/health-check"]


def test_mineru_official_rejects_private_provider_url(monkeypatch):
    monkeypatch.setattr(
        "yuxi.knowledge.parser.mineru_official.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("127.0.0.1", port))],
    )

    with pytest.raises(DocumentParserException, match="URL 无效"):
        MinerUOfficialParser._validate_provider_url("https://internal.example/result.zip")


def test_pp_structure_api_error_does_not_expose_provider_body(monkeypatch):
    captured_logger = CapturedLogger()
    response = FakeResponse(status_code=500, text=SECRET)

    class FakeSession:
        trust_env = False

        def post(self, *args, **kwargs):
            return response

        def close(self) -> None:
            return None

    parser = PPStructureV3Parser(server_url="http://paddlex.test")
    parser._session.close()
    parser._session = FakeSession()
    monkeypatch.setattr("yuxi.knowledge.parser.pp_structure_v3.logger", captured_logger)

    with pytest.raises(DocumentParserException) as exc_info:
        parser._call_layout_api("base64-input")

    assert response.closed is True
    assert SECRET not in str(exc_info.value)
    assert SECRET not in " ".join(captured_logger.messages)


def test_rapid_ocr_closes_pdf_and_page_image_after_failure(monkeypatch, tmp_path: Path):
    closed = {"document": False, "image": False}

    class FakePixmap:
        width = 1
        height = 1
        samples = b"\x00\x00\x00"

    class FakePage:
        def get_pixmap(self, **kwargs):
            return FakePixmap()

    class FakeDocument:
        page_count = 1

        def __getitem__(self, index: int):
            return FakePage()

        def close(self) -> None:
            closed["document"] = True

    class FakeImage:
        def close(self) -> None:
            closed["image"] = True

    file_path = tmp_path / "document.pdf"
    file_path.write_bytes(b"pdf")
    parser = RapidOCRParser()
    parser.process_image = lambda image: (_ for _ in ()).throw(RuntimeError(SECRET))
    monkeypatch.setattr("yuxi.knowledge.parser.rapid_ocr.fitz.open", lambda path: FakeDocument())
    monkeypatch.setattr("yuxi.knowledge.parser.rapid_ocr.Image.frombytes", lambda *args, **kwargs: FakeImage())

    with pytest.raises(OCRException) as exc_info:
        parser.process_pdf(str(file_path))

    assert str(exc_info.value) == "[rapid_ocr] PDF OCR 处理失败"
    assert closed == {"document": True, "image": True}
