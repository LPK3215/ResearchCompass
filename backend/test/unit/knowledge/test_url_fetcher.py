from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from yuxi.knowledge.utils import url_fetcher


class _NetworkStream:
    def __init__(self, address: str):
        self.address = address

    def get_extra_info(self, name: str):
        assert name == "server_addr"
        return (self.address, 443)


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"<html>ok</html>",
        peer_address: str = "93.184.216.34",
    ):
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html", "content-length": str(len(body))}
        self.extensions = {"network_stream": _NetworkStream(peer_address)}
        self.body = body
        self.body_iterated = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://allowed.example/page")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("request failed", request=request, response=response)

    async def aiter_bytes(self):
        self.body_iterated = True
        yield self.body


class _AsyncClient:
    def __init__(self, responses: list[_Response]):
        self.responses = list(responses)
        self.calls: list[str] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def stream(self, method: str, url: str, *, headers: dict[str, str]):
        assert method == "GET"
        assert headers["User-Agent"]
        self.calls.append(url)
        return self.responses.pop(0)


def _allow_fetching(monkeypatch: pytest.MonkeyPatch, *, private_hosts: set[str] | None = None) -> None:
    blocked = private_hosts or set()
    monkeypatch.setattr(url_fetcher, "is_url_parsing_enabled", lambda: True)
    monkeypatch.setattr(url_fetcher, "validate_url", lambda url: (True, ""))

    async def private_ip(hostname: str) -> bool:
        return hostname in blocked

    monkeypatch.setattr(url_fetcher, "is_private_ip", private_ip)


@pytest.mark.asyncio
async def test_hostname_resolution_failure_is_blocked(monkeypatch: pytest.MonkeyPatch):
    def fail_resolution(*args, **kwargs):
        del args, kwargs
        raise OSError("resolver unavailable")

    monkeypatch.setattr(url_fetcher.socket, "getaddrinfo", fail_resolution)

    assert await url_fetcher.is_private_ip("allowed.example") is True


@pytest.mark.asyncio
async def test_fetch_ignores_environment_proxy_and_closes_client(monkeypatch: pytest.MonkeyPatch):
    _allow_fetching(monkeypatch)
    captured = SimpleNamespace(kwargs=None)
    client = _AsyncClient([_Response()])

    def async_client(**kwargs):
        captured.kwargs = kwargs
        return client

    monkeypatch.setattr(url_fetcher.httpx, "AsyncClient", async_client)

    content, final_url = await url_fetcher.fetch_url_content("https://allowed.example/page")

    assert content == b"<html>ok</html>"
    assert final_url == "https://allowed.example/page"
    assert captured.kwargs["trust_env"] is False
    assert client.closed is True


@pytest.mark.asyncio
async def test_fetch_rejects_private_connected_peer_after_public_dns_check(monkeypatch: pytest.MonkeyPatch):
    _allow_fetching(monkeypatch)
    client = _AsyncClient([_Response(peer_address="127.0.0.1")])
    monkeypatch.setattr(url_fetcher.httpx, "AsyncClient", lambda **kwargs: client)

    with pytest.raises(ValueError, match="verifiable public address"):
        await url_fetcher.fetch_url_content("https://allowed.example/page")


@pytest.mark.asyncio
async def test_redirect_target_is_revalidated_before_second_connection(monkeypatch: pytest.MonkeyPatch):
    _allow_fetching(monkeypatch, private_hosts={"internal.allowed.example"})
    client = _AsyncClient(
        [
            _Response(
                status_code=302,
                headers={"location": "https://internal.allowed.example/admin"},
            )
        ]
    )
    monkeypatch.setattr(url_fetcher.httpx, "AsyncClient", lambda **kwargs: client)

    with pytest.raises(ValueError, match="private or unresolvable"):
        await url_fetcher.fetch_url_content("https://allowed.example/page")

    assert client.calls == ["https://allowed.example/page"]


@pytest.mark.asyncio
async def test_declared_oversized_response_is_rejected_before_body_read(monkeypatch: pytest.MonkeyPatch):
    _allow_fetching(monkeypatch)
    response = _Response(headers={"content-type": "text/html", "content-length": "11"})
    client = _AsyncClient([response])
    monkeypatch.setattr(url_fetcher.httpx, "AsyncClient", lambda **kwargs: client)

    with pytest.raises(ValueError, match="Content size exceeds"):
        await url_fetcher.fetch_url_content("https://allowed.example/page", max_size=10)

    assert response.body_iterated is False
