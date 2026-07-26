from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.services import semantic_scholar_service
from yuxi.services.semantic_scholar_service import SemanticScholarClient, SemanticScholarError


class _NetworkStream:
    def __init__(self, address: str):
        self.address = address

    def get_extra_info(self, name: str):
        assert name == "server_addr"
        return (self.address, 443)


class _Response:
    def __init__(self, *, peer_address: str, content: bytes = b"%PDF-test"):
        self.status_code = 200
        self.headers = {"content-type": "application/pdf", "content-length": str(len(content))}
        self.extensions = {"network_stream": _NetworkStream(peer_address)}
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self):
        yield self._content


class _AsyncClient:
    def __init__(self, response: _Response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str):
        assert method == "GET"
        assert url == "https://papers.example/paper.pdf"
        return self.response


class _JsonResponse:
    def __init__(self, status_code: int, *, retry_after: str | None = None):
        self.status_code = status_code
        self.headers = {"Retry-After": retry_after} if retry_after is not None else {}

    def json(self):
        return {"paperId": "paper-1"}


class _GetAsyncClient:
    def __init__(self, responses: list[_JsonResponse]):
        self.responses = list(responses)
        self.get_calls = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    async def get(self, url: str, params=None):
        assert url.endswith("/paper/test")
        assert params == {"fields": "paperId"}
        response = self.responses[self.get_calls]
        self.get_calls += 1
        return response


@pytest.mark.asyncio
async def test_pdf_host_validation_rejects_shared_non_public_address():
    assert await semantic_scholar_service._is_private_or_unresolvable_host("100.64.0.1") is True


@pytest.mark.asyncio
async def test_pdf_download_rejects_private_connected_peer_after_public_dns_check(monkeypatch):
    async def public_dns(_hostname: str) -> bool:
        return False

    monkeypatch.setattr(semantic_scholar_service, "_is_private_or_unresolvable_host", public_dns)
    monkeypatch.setattr(
        semantic_scholar_service.httpx,
        "AsyncClient",
        lambda **kwargs: _AsyncClient(_Response(peer_address="127.0.0.1")),
    )

    with pytest.raises(SemanticScholarError) as exc_info:
        await SemanticScholarClient().download_open_access_pdf("https://papers.example/paper.pdf")

    assert exc_info.value.error_type == "paper_pdf_private_network"


@pytest.mark.asyncio
async def test_pdf_download_ignores_environment_proxy_settings(monkeypatch):
    captured = SimpleNamespace(kwargs=None)

    async def public_dns(_hostname: str) -> bool:
        return False

    def async_client(**kwargs):
        captured.kwargs = kwargs
        return _AsyncClient(_Response(peer_address="93.184.216.34"))

    monkeypatch.setattr(semantic_scholar_service, "_is_private_or_unresolvable_host", public_dns)
    monkeypatch.setattr(semantic_scholar_service.httpx, "AsyncClient", async_client)

    content, final_url = await SemanticScholarClient().download_open_access_pdf(
        "https://papers.example/paper.pdf"
    )

    assert content == b"%PDF-test"
    assert final_url == "https://papers.example/paper.pdf"
    assert captured.kwargs["trust_env"] is False


@pytest.mark.asyncio
async def test_semantic_scholar_get_reuses_client_and_caps_retry_after(monkeypatch):
    captured = SimpleNamespace(kwargs=None)
    client = _GetAsyncClient(
        [
            _JsonResponse(429, retry_after="999999"),
            _JsonResponse(200),
        ]
    )
    sleeps: list[float] = []

    def async_client(**kwargs):
        captured.kwargs = kwargs
        return client

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    monkeypatch.setattr(semantic_scholar_service.httpx, "AsyncClient", async_client)
    monkeypatch.setattr(semantic_scholar_service.asyncio, "sleep", fake_sleep)

    result = await SemanticScholarClient()._get("/paper/test", {"fields": "paperId"})

    assert result == {"paperId": "paper-1"}
    assert client.get_calls == 2
    assert client.closed is True
    assert sleeps == [semantic_scholar_service.MAX_SEMANTIC_SCHOLAR_RETRY_SECONDS]
    assert captured.kwargs["trust_env"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["invalid", "nan", "-1"])
async def test_semantic_scholar_get_uses_bounded_fallback_for_invalid_retry_after(
    monkeypatch,
    retry_after: str,
):
    client = _GetAsyncClient([_JsonResponse(429, retry_after=retry_after), _JsonResponse(200)])
    sleeps: list[float] = []

    monkeypatch.setattr(semantic_scholar_service.httpx, "AsyncClient", lambda **_kwargs: client)

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    monkeypatch.setattr(semantic_scholar_service.asyncio, "sleep", fake_sleep)

    await SemanticScholarClient()._get("/paper/test", {"fields": "paperId"})

    assert sleeps == [10]
