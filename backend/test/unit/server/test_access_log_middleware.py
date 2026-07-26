from starlette.requests import Request
from starlette.responses import Response

from server.utils.access_log_middleware import AccessLogMiddleware


class RecordingLogger:
    def __init__(self):
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)


async def test_access_log_omits_query_values():
    logger = RecordingLogger()
    middleware = AccessLogMiddleware(app=lambda scope, receive, send: None, logger=logger)
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/research/external-papers/search",
            "raw_path": b"/api/research/external-papers/search",
            "query_string": b"query=confidential-research-topic&token=secret-token",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )

    async def call_next(_request: Request) -> Response:
        return Response(status_code=200)

    await middleware.dispatch(request, call_next)

    assert len(logger.messages) == 1
    assert "GET /api/research/external-papers/search HTTP/1.1" in logger.messages[0]
    assert "confidential-research-topic" not in logger.messages[0]
    assert "secret-token" not in logger.messages[0]
