import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.responses import StreamingResponse

from server import main


@pytest.mark.asyncio
async def test_unexpected_http_failure_returns_fixed_detail_without_logging_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    log_entries: list[tuple[str, tuple, dict]] = []

    class FakeLogger:
        def __getattr__(self, level):
            def record(*args, **kwargs):
                log_entries.append((level, args, kwargs))

            return record

    app = FastAPI()
    app.add_middleware(main.SafeExceptionMiddleware)

    @app.get("/boom")
    async def boom():
        raise RuntimeError(secret)

    monkeypatch.setattr(main, "logger", FakeLogger())

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "服务暂时不可用，请稍后重试"}
    assert secret not in response.text
    assert secret not in repr(log_entries)


def test_main_app_registers_safe_exception_middleware_as_outermost_user_boundary():
    assert main.app.user_middleware[0].cls is main.SafeExceptionMiddleware


@pytest.mark.asyncio
async def test_stream_failure_closes_response_without_exposing_or_reraising_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    log_entries: list[tuple[str, tuple, dict]] = []

    class FakeLogger:
        def __getattr__(self, level):
            def record(*args, **kwargs):
                log_entries.append((level, args, kwargs))

            return record

    app = FastAPI()
    app.add_middleware(main.SafeExceptionMiddleware)

    @app.get("/stream")
    async def stream():
        async def body():
            yield b"started"
            raise RuntimeError(secret)

        return StreamingResponse(body())

    monkeypatch.setattr(main, "logger", FakeLogger())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/stream")

    assert response.status_code == 200
    assert response.content == b"started"
    assert secret not in repr(log_entries)
