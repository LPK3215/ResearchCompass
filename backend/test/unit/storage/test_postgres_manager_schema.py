from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.exceptions import RequestValidationError

from yuxi.storage.postgres import manager as postgres_manager
from yuxi.storage.postgres.manager import PostgresManager, _redact_database_url


class _RecordingConnection:
    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))


class _RecordingBegin:
    def __init__(self, connection: _RecordingConnection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RecordingEngine:
    def __init__(self, connection: _RecordingConnection):
        self.connection = connection

    def begin(self):
        return _RecordingBegin(self.connection)

    def connect(self):
        return _RecordingBegin(self.connection)


class _HTTPStyleError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"{status_code}: expected HTTP error")
        self.status_code = status_code


class _RecordingLangGraphPool:
    def __init__(self, *, closed: bool):
        self.closed = closed
        self.open_calls: list[bool] = []

    async def open(self, *, wait: bool):
        self.open_calls.append(wait)
        self.closed = False


class _RecordingSession:
    def __init__(self):
        self.calls: list[str] = []

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        await asyncio.sleep(0)
        self.calls.append("rollback")

    async def close(self):
        await asyncio.sleep(0)
        self.calls.append("close")


@pytest.mark.parametrize(
    ("database_url", "expected"),
    [
        (
            "postgresql+asyncpg://research_user:super%40secret@postgres:5432/research_compass",
            "postgresql+asyncpg://research_user:***@postgres:5432/research_compass",
        ),
        (
            "postgresql+asyncpg://postgres:5432/research_compass",
            "postgresql+asyncpg://postgres:5432/research_compass",
        ),
    ],
)
def test_redact_database_url_never_exposes_password(database_url, expected):
    redacted = _redact_database_url(database_url)

    assert redacted == expected
    assert "super%40secret" not in redacted


def test_redact_database_url_does_not_echo_invalid_input():
    secret = "not a URL containing secret-password"

    redacted = _redact_database_url(secret)

    assert redacted == "<invalid database URL>"
    assert secret not in redacted


def test_log_async_session_rollback_demotes_expected_4xx_http_errors(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        postgres_manager,
        "logger",
        SimpleNamespace(
            debug=lambda message: calls.append(("debug", message)),
            error=lambda message: calls.append(("error", message)),
        ),
    )

    postgres_manager._log_async_session_rollback(_HTTPStyleError(409))

    assert len(calls) == 1
    assert calls[0][0] == "debug"
    assert "expected HTTP 409" in calls[0][1]


def test_log_async_session_rollback_redacts_request_validation_input(monkeypatch):
    secret = "validation-secret-must-not-be-logged"
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        postgres_manager,
        "logger",
        SimpleNamespace(
            debug=lambda message: calls.append(("debug", message)),
            error=lambda message: calls.append(("error", message)),
        ),
    )
    error = RequestValidationError(
        [
            {
                "type": "string_too_short",
                "loc": ("body", "password"),
                "msg": "String should have at least 8 characters",
                "input": secret,
            }
        ]
    )

    postgres_manager._log_async_session_rollback(error)

    assert calls == [
        (
            "debug",
            "PostgreSQL async operation rolled back for request validation "
            "(error_type=RequestValidationError, error_count=1)",
        )
    ]
    assert secret not in str(calls)


@pytest.mark.asyncio
async def test_async_session_context_rolls_back_and_closes_when_cancelled():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_session_factory = manager.AsyncSession
    session = _RecordingSession()
    entered = asyncio.Event()
    manager._initialized = True
    manager.AsyncSession = lambda: session

    async def use_session():
        async with manager.get_async_session_context():
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(use_session())
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        manager._initialized = original_initialized
        manager.AsyncSession = original_session_factory

    assert session.calls == ["rollback", "close"]


@pytest.mark.asyncio
async def test_open_langgraph_pool_opens_only_a_closed_pool():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_pool = manager.langgraph_pool
    pool = _RecordingLangGraphPool(closed=True)
    manager._initialized = True
    manager.langgraph_pool = pool
    try:
        await manager.open_langgraph_pool()
        await manager.open_langgraph_pool()
    finally:
        manager._initialized = original_initialized
        manager.langgraph_pool = original_pool

    assert pool.open_calls == [True]


@pytest.mark.asyncio
async def test_schema_initialization_lock_acquires_and_releases_postgres_advisory_lock():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()
    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        async with manager.schema_initialization_lock():
            connection.statements.append("schema migration")
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    assert "pg_advisory_lock" in connection.statements[0]
    assert connection.statements[1] == "schema migration"
    assert "pg_advisory_unlock" in connection.statements[2]


@pytest.mark.parametrize("error", [_HTTPStyleError(500), RuntimeError("database is unavailable")])
def test_log_async_session_rollback_keeps_unexpected_errors_at_error_level(error, monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        postgres_manager,
        "logger",
        SimpleNamespace(
            debug=lambda message: calls.append(("debug", message)),
            error=lambda message: calls.append(("error", message)),
        ),
    )

    postgres_manager._log_async_session_rollback(error)

    assert len(calls) == 1
    assert calls[0][0] == "error"


@pytest.mark.asyncio
async def test_ensure_business_schema_backfills_subagent_thread_columns_before_dropping_legacy_columns():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "SET agent_slug = agent_id" in statements
    assert "SET conversation_thread_id = thread_id" in statements
    assert "SET created_by_run_id = COALESCE(parent_agent_run_id, parent_run_id)" in statements
    assert "SET subagent_slug = c.agent_id" in statements
    assert "SET created_by_run_id = created_by_parent_run_id::VARCHAR" in statements
    assert "ALTER COLUMN subagent_slug SET NOT NULL" in statements
    assert "ALTER COLUMN created_by_run_id SET NOT NULL" in statements
    assert statements.index("SET agent_slug = agent_id") < statements.index("DROP COLUMN IF EXISTS agent_id")
    assert statements.index("SET conversation_thread_id = thread_id") < statements.index(
        "DROP COLUMN IF EXISTS thread_id"
    )
    assert statements.index("COALESCE(parent_agent_run_id, parent_run_id)") < statements.index(
        "DROP COLUMN IF EXISTS parent_agent_run_id"
    )
    assert statements.index("created_by_parent_run_id") < statements.index(
        "DROP COLUMN IF EXISTS created_by_parent_run_id"
    )


@pytest.mark.asyncio
async def test_ensure_business_schema_cleans_duplicate_active_agent_runs_before_unique_index():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "WITH duplicated_active_runs AS" in statements
    assert "active_run_migration_conflict" in statements
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_per_thread" in statements
    assert statements.index("WITH duplicated_active_runs AS") < statements.index(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_per_thread"
    )


@pytest.mark.asyncio
async def test_ensure_business_schema_creates_user_config_table():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "CREATE TABLE IF NOT EXISTS user_config" in statements
    assert "enable_memory BOOLEAN NOT NULL DEFAULT FALSE" in statements


@pytest.mark.asyncio
async def test_ensure_business_schema_removes_unbound_api_keys_before_requiring_user_id():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "UPDATE cli_auth_sessions" in statements
    assert "DELETE FROM api_keys WHERE user_id IS NULL" in statements
    assert "ALTER TABLE IF EXISTS api_keys ALTER COLUMN user_id SET NOT NULL" in statements
    assert statements.index("UPDATE cli_auth_sessions") < statements.index("DELETE FROM api_keys WHERE user_id IS NULL")
    assert statements.index("DELETE FROM api_keys WHERE user_id IS NULL") < statements.index(
        "ALTER TABLE IF EXISTS api_keys ALTER COLUMN user_id SET NOT NULL"
    )


@pytest.mark.asyncio
async def test_ensure_knowledge_schema_adds_research_runtime_columns_and_indexes():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_knowledge_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "academic_graph_sync_runs ADD COLUMN IF NOT EXISTS processed_paper_ids" in statements
    assert "research_search_runs ADD COLUMN IF NOT EXISTS result_snapshot JSONB" in statements
    assert "research_search_runs ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE" in statements
    assert "tasks ADD COLUMN IF NOT EXISTS retryable BOOLEAN NOT NULL DEFAULT FALSE" in statements
    assert "tasks ADD COLUMN IF NOT EXISTS dependency VARCHAR(64)" in statements
    assert "tasks ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0" in statements
    assert "CREATE INDEX IF NOT EXISTS ix_research_search_runs_history" in statements
    assert "ON research_search_runs (kb_id, uid, is_pinned, created_at)" in statements
    assert "ck_research_evidence_status" in statements
    assert "ix_research_evidence_kb_status" in statements
    assert "ix_research_evidence_activities_evidence_created" in statements
