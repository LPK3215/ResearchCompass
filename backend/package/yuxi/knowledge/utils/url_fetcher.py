import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from yuxi.knowledge.utils.url_validator import is_url_parsing_enabled, validate_url
from yuxi.utils import logger

# 最大允许下载大小 (例如 10MB)
MAX_DOWNLOAD_SIZE = 10 * 1024 * 1024
# 允许的 Content-Type
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
MAX_REDIRECTS = 5


def _is_public_ip_address(value: Any) -> bool:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    return address.is_global


async def is_private_ip(hostname: str) -> bool:
    """Return True when a host cannot be proven to resolve only to public IPs."""
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("URL hostname resolution failed: exception_type={}", type(exc).__name__)
        return True
    return not addresses or any(not _is_public_ip_address(item[4][0]) for item in addresses)


def _response_has_public_peer(response: httpx.Response) -> bool:
    network_stream = response.extensions.get("network_stream")
    if network_stream is None:
        return False
    try:
        server_address = network_stream.get_extra_info("server_addr")
    except Exception:  # noqa: BLE001
        return False
    if isinstance(server_address, (tuple, list)) and server_address:
        server_address = server_address[0]
    return _is_public_ip_address(server_address)


async def _validate_fetch_target(url: str, *, redirected: bool = False) -> None:
    is_valid, error_message = validate_url(url)
    if not is_valid:
        prefix = "Redirected to invalid URL" if redirected else "Invalid URL"
        raise ValueError(f"{prefix}: {error_message}")

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValueError("Invalid URL") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are forbidden")
    if not parsed.hostname or await is_private_ip(parsed.hostname):
        message = "Redirected to private or unresolvable address" if redirected else "Private or unresolvable address"
        raise ValueError(message)


async def fetch_url_content(url: str, max_size: int = MAX_DOWNLOAD_SIZE) -> tuple[bytes, str]:
    """Fetch whitelisted public HTML with redirect, peer and size checks."""
    if not is_url_parsing_enabled():
        raise ValueError("URL parsing feature is disabled")
    if max_size <= 0:
        raise ValueError("Maximum download size must be positive")

    current_url = url
    redirect_count = 0
    timeout = httpx.Timeout(30.0, connect=10.0)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            while True:
                await _validate_fetch_target(current_url, redirected=redirect_count > 0)
                logger.info("Fetching whitelisted URL (redirect_count={})", redirect_count)

                async with client.stream("GET", current_url, headers=headers) as response:
                    if not _response_has_public_peer(response):
                        raise ValueError("URL connection did not reach a verifiable public address")

                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= MAX_REDIRECTS:
                            raise ValueError("Too many redirects")
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Redirect response missing Location header")
                        current_url = urljoin(current_url, location)
                        redirect_count += 1
                        continue

                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in ALLOWED_CONTENT_TYPES:
                        raise ValueError("Unsupported Content-Type. Only HTML is supported")

                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_size = int(content_length)
                        except ValueError as exc:
                            raise ValueError("Invalid Content-Length header") from exc
                        if declared_size < 0:
                            raise ValueError("Invalid Content-Length header")
                        if declared_size > max_size:
                            raise ValueError(f"Content size exceeds limit of {max_size} bytes")

                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_size:
                            raise ValueError(f"Content size exceeds limit of {max_size} bytes")
                    return bytes(content), current_url
    except ValueError:
        raise
    except httpx.TimeoutException as exc:
        logger.warning("URL fetch timed out: exception_type={}", type(exc).__name__)
        raise ValueError("URL request timed out") from None
    except httpx.HTTPError as exc:
        logger.warning("URL fetch failed: exception_type={}", type(exc).__name__)
        raise ValueError("Failed to fetch URL") from None
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected URL fetch failure: exception_type={}", type(exc).__name__)
        raise ValueError("Failed to fetch URL") from None
