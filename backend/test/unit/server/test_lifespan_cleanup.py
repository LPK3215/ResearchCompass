import pytest

from server.utils import lifespan as lifespan_module
from yuxi.services import langfuse_service


@pytest.mark.asyncio
async def test_shutdown_resources_continues_after_failure_and_sanitizes_logs(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    calls: list[str] = []
    messages: list[str] = []

    async def fail_tasker_shutdown():
        calls.append("tasker")
        raise RuntimeError(secret)

    def stop_runtime_sync():
        calls.append("runtime_sync")

    def clear_parser_cache():
        calls.append("parser_cache")

    def close_langfuse():
        calls.append("langfuse")

    def stop_sandbox():
        calls.append("sandbox")

    async def close_queue_clients():
        calls.append("queue")

    def close_neo4j():
        calls.append("neo4j")

    async def close_postgres():
        calls.append("postgres")

    monkeypatch.setattr(lifespan_module.tasker, "shutdown", fail_tasker_shutdown)
    monkeypatch.setattr(lifespan_module.config, "stop_runtime_sync", stop_runtime_sync)
    monkeypatch.setattr(lifespan_module.DocumentProcessorFactory, "clear_cache", clear_parser_cache)
    monkeypatch.setattr(langfuse_service, "close_langfuse_client", close_langfuse)
    monkeypatch.setattr(lifespan_module, "shutdown_sandbox_provider", stop_sandbox)
    monkeypatch.setattr(lifespan_module, "close_queue_clients", close_queue_clients)
    monkeypatch.setattr(lifespan_module, "close_shared_neo4j_connection", close_neo4j)
    monkeypatch.setattr(lifespan_module.pg_manager, "close", close_postgres)
    monkeypatch.setattr(lifespan_module.logger, "error", messages.append)

    await lifespan_module._shutdown_resources()

    assert calls == [
        "tasker",
        "runtime_sync",
        "parser_cache",
        "langfuse",
        "sandbox",
        "queue",
        "neo4j",
        "postgres",
    ]
    assert secret not in " ".join(messages)
