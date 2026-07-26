from __future__ import annotations

from fastapi import Request

import pytest

from server.utils.client_ip import extract_client_ip, validate_trusted_proxy_cidrs


def _request(*, peer: str, forwarded_for: str | None = None) -> Request:
    headers = [] if forwarded_for is None else [(b"x-forwarded-for", forwarded_for.encode("ascii"))]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("testserver", 80),
        }
    )


def test_extract_client_ip_ignores_spoofed_forwarding_from_an_untrusted_peer(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)

    result = extract_client_ip(_request(peer="198.51.100.10", forwarded_for="203.0.113.9"))

    assert result == "198.51.100.10"


def test_extract_client_ip_uses_the_rightmost_non_proxy_forwarded_address(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8,192.0.2.0/24")

    result = extract_client_ip(
        _request(peer="10.0.0.20", forwarded_for="203.0.113.9, 198.51.100.7, 192.0.2.5")
    )

    assert result == "198.51.100.7"


def test_extract_client_ip_keeps_the_proxy_peer_for_an_invalid_forwarded_chain(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    result = extract_client_ip(_request(peer="10.0.0.20", forwarded_for="not-an-ip"))

    assert result == "10.0.0.20"


def test_extract_client_ip_canonicalizes_forwarded_ipv6_for_rate_limit_keys(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    result = extract_client_ip(
        _request(peer="10.0.0.20", forwarded_for="2001:4860:4860:0000:0000:0000:0000:8888")
    )

    assert result == "2001:4860:4860::8888"


def test_validate_trusted_proxy_cidrs_rejects_invalid_configuration(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8,not-a-cidr")

    with pytest.raises(ValueError, match="无效 CIDR"):
        validate_trusted_proxy_cidrs()
