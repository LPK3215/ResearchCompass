from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable

import pytest
from fastapi import Request

import server.main as main_module
from server.main import (
    RATE_LIMIT_MAX_ATTEMPTS,
    RATE_LIMIT_WINDOW_SECONDS,
    LoginRateLimitMiddleware,
    _login_attempts,
    _prune_login_attempts,
)


def _request(peer: str, *, path: str = "/api/auth/token") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": (peer, 12345),
            "server": ("testserver", 80),
        }
    )


def _call_next_with(status_code: int) -> Callable[[Request], Awaitable[object]]:
    class _Response:
        def __init__(self, code: int) -> None:
            self.status_code = code

    async def call_next(_request: Request) -> _Response:
        return _Response(status_code)

    return call_next


@pytest.fixture(autouse=True)
def _reset_login_attempts():
    _login_attempts.clear()
    yield
    _login_attempts.clear()


@pytest.mark.asyncio
async def test_tracked_sources_stay_within_the_configured_limit(monkeypatch) -> None:
    """未认证请求不断更换源地址时，跟踪表不能无界增长。"""
    monkeypatch.setattr(main_module, "RATE_LIMIT_TRACKED_SOURCES_LIMIT", 50)
    middleware = LoginRateLimitMiddleware(app=None)
    call_next = _call_next_with(401)

    for index in range(500):
        await middleware.dispatch(_request(f"10.0.{index // 256}.{index % 256}"), call_next)

    assert len(_login_attempts) <= 50


@pytest.mark.asyncio
async def test_prune_keeps_recent_sources_and_drops_stale_ones(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "RATE_LIMIT_TRACKED_SOURCES_LIMIT", 2)
    now = 1_000.0
    _login_attempts["active"] = deque([now - 1])
    for index in range(3):
        _login_attempts[f"stale-{index}"] = deque([now - RATE_LIMIT_WINDOW_SECONDS - 5])

    _prune_login_attempts(now)

    assert "active" in _login_attempts
    assert len(_login_attempts) <= 2


@pytest.mark.asyncio
async def test_prune_enforces_the_limit_when_every_source_is_recent(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "RATE_LIMIT_TRACKED_SOURCES_LIMIT", 2)
    now = 1_000.0
    for index in range(5):
        _login_attempts[f"recent-{index}"] = deque([now - index])

    _prune_login_attempts(now)

    # 剪枝在写入新来源之前执行，因此留下的是 limit - 1 条，写入后仍不超上界。
    assert len(_login_attempts) == 1


@pytest.mark.asyncio
async def test_login_attempts_are_still_limited_per_source() -> None:
    middleware = LoginRateLimitMiddleware(app=None)
    call_next = _call_next_with(401)
    request = _request("198.51.100.10")

    for _ in range(RATE_LIMIT_MAX_ATTEMPTS):
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401

    blocked = await middleware.dispatch(request, call_next)

    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]


@pytest.mark.asyncio
async def test_successful_login_clears_the_source_history() -> None:
    middleware = LoginRateLimitMiddleware(app=None)
    request = _request("198.51.100.11")

    for _ in range(RATE_LIMIT_MAX_ATTEMPTS - 1):
        await middleware.dispatch(request, _call_next_with(401))
    await middleware.dispatch(request, _call_next_with(200))

    assert "198.51.100.11" not in _login_attempts

    for _ in range(RATE_LIMIT_MAX_ATTEMPTS):
        response = await middleware.dispatch(request, _call_next_with(401))
        assert response.status_code == 401
