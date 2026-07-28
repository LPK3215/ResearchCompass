import asyncio
import os
import sys

# ==============================================================================
# 解决 Windows 下 psycopg 异步模式不支持 ProactorEventLoop 的问题
# 注意：这段代码必须放在应用的极早期，最好在导入 FastAPI 或初始化数据库之前
# ==============================================================================
if sys.platform == "win32":
    # 把当前文件 (main.py) 的上一级的上一级 (即项目根目录) 加入到 sys.path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import time
from collections import defaultdict, deque

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from server.routers import router
from server.utils.client_ip import extract_client_ip
from server.utils.lifespan import lifespan
from server.utils.common_utils import setup_logging
from server.utils.access_log_middleware import AccessLogMiddleware
from yuxi.utils.logging_config import logger

# 设置日志配置
setup_logging()

RATE_LIMIT_MAX_ATTEMPTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_ENDPOINTS = {("/api/auth/token", "POST"), ("/api/auth/register", "POST")}
DEFAULT_DEVELOPMENT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
EXPLICIT_CORS_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")
EXPLICIT_CORS_HEADERS = ("Accept", "Authorization", "Content-Type", "Last-Event-ID", "X-Requested-With")

# In-memory login attempt tracker to reduce brute-force exposure per worker
_login_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_attempt_lock = asyncio.Lock()


def _parse_cors_origins() -> list[str]:
    value = os.getenv("YUXI_CORS_ORIGINS")
    origins = [origin.strip() for origin in (value or "").split(",") if origin.strip()]
    if origins:
        return origins

    environment = (os.getenv("YUXI_ENV") or "development").strip().lower()
    if environment in {"production", "prod"}:
        return []

    return list(DEFAULT_DEVELOPMENT_CORS_ORIGINS)


def _build_cors_options(origins: list[str] | None = None) -> dict[str, object]:
    allow_origins = _parse_cors_origins() if origins is None else origins
    if "*" in allow_origins:
        return {
            "allow_origins": ["*"],
            "allow_credentials": False,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }

    return {
        "allow_origins": allow_origins,
        "allow_credentials": True,
        "allow_methods": list(EXPLICIT_CORS_METHODS),
        "allow_headers": list(EXPLICIT_CORS_HEADERS),
        "expose_headers": ["Content-Disposition", "X-Lock-Remaining"],
    }


app = FastAPI(
    lifespan=lifespan,
    title="ResearchCompass API",
    description="科研罗盘 - 基于 RAG 与知识图谱的科研智能体平台。融合论文管理、学术检索、证据综述、研究项目、引用图谱、论文分析等科研业务流程。",
    version="0.11.0",
    openapi_tags=[
        {"name": "authentication", "description": "用户认证与账户管理"},
        {"name": "knowledge", "description": "知识库管理与文档检索"},
        {"name": "research", "description": "科研业务：论文、检索、综述、项目、图谱"},
        {"name": "chat", "description": "智能体对话与会话管理"},
        {"name": "agent", "description": "智能体配置与运行管理"},
    ],
)


class SafeExceptionMiddleware:
    """Close the HTTP error boundary without forwarding exception bodies to the ASGI server log."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False
        response_completed = False

        async def send_wrapper(message):
            nonlocal response_completed, response_started
            if message["type"] == "http.response.start":
                response_started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_completed = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            route = scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            logger.error(
                "Unhandled HTTP failure "
                f"(method={scope.get('method')}, route={route_path}, "
                f"response_started={response_started}, error_type={type(exc).__name__})"
            )
            if response_started:
                if not response_completed:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "服务暂时不可用，请稍后重试"},
            )
            await response(scope, receive, send)


# 所有业务接口统一挂载到 /api，具体分组在 server.routers 中集中注册。
app.include_router(router, prefix="/api")

# CORS 设置
app.add_middleware(
    CORSMiddleware,
    **_build_cors_options(),
)


def _extract_client_ip(request: Request) -> str:
    return extract_client_ip(request)


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        normalized_path = request.url.path.rstrip("/") or "/"
        request_signature = (normalized_path, request.method.upper())

        if request_signature in RATE_LIMIT_ENDPOINTS:
            client_ip = _extract_client_ip(request)
            now = time.monotonic()

            async with _attempt_lock:
                attempt_history = _login_attempts[client_ip]

                while attempt_history and now - attempt_history[0] > RATE_LIMIT_WINDOW_SECONDS:
                    attempt_history.popleft()

                if len(attempt_history) >= RATE_LIMIT_MAX_ATTEMPTS:
                    retry_after = int(max(1, RATE_LIMIT_WINDOW_SECONDS - (now - attempt_history[0])))
                    return JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={"detail": "登录尝试过于频繁，请稍后再试"},
                        headers={"Retry-After": str(retry_after)},
                    )

                attempt_history.append(now)

            response = await call_next(request)

            if response.status_code < 400:
                async with _attempt_lock:
                    _login_attempts.pop(client_ip, None)

            return response

        return await call_next(request)


# 添加访问日志中间件（记录请求处理时间）
app.add_middleware(AccessLogMiddleware)

# 添加登录限流中间件
app.add_middleware(LoginRateLimitMiddleware)

# 最外层业务异常边界，避免未知异常正文进入 HTTP 响应或 ASGI 服务器 traceback。
app.add_middleware(SafeExceptionMiddleware)

if __name__ == "__main__":
    # uvicorn.run(app, host="0.0.0.0", port=5050, threads=10, workers=10, reload=True)

    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=5050,
        reload=True,
        # 与 docker-compose 开发环境保持一致，避免 package 下代码变更不触发热重载。
        reload_dirs=["server", "package"],
    )
