"""Trusted-proxy aware client address extraction for public rate limits and logs."""

from __future__ import annotations

import ipaddress
import os

from fastapi import Request


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    values = os.getenv("TRUSTED_PROXY_CIDRS", "")
    networks = []
    for value in values.split(","):
        normalized = value.strip()
        if not normalized:
            continue
        try:
            networks.append(ipaddress.ip_network(normalized, strict=False))
        except ValueError as exc:
            raise ValueError(f"TRUSTED_PROXY_CIDRS 包含无效 CIDR: {normalized}") from exc
    return tuple(networks)


def _is_trusted_proxy(address: str, networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def extract_client_ip(request: Request) -> str:
    """Return the direct peer unless it is an explicitly configured proxy."""
    peer = request.client.host if request.client else "unknown"
    try:
        normalized_peer = str(ipaddress.ip_address(peer))
    except ValueError:
        normalized_peer = peer
    networks = _trusted_proxy_networks()
    if not networks or not _is_trusted_proxy(normalized_peer, networks):
        return normalized_peer

    forwarded_for = request.headers.get("x-forwarded-for", "")
    for value in reversed(forwarded_for.split(",")):
        address = value.strip()
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if not _is_trusted_proxy(address, networks):
            return str(parsed)
    return normalized_peer


def validate_trusted_proxy_cidrs() -> None:
    _trusted_proxy_networks()


__all__ = ["extract_client_ip", "validate_trusted_proxy_cidrs"]
