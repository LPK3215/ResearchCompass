import pytest

from yuxi.services import operation_log_service


@pytest.mark.asyncio
async def test_log_operation_reports_persistence_failure_without_raising(monkeypatch):
    messages = []

    class FailingSession:
        def add(self, _entry):
            return None

        async def commit(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(operation_log_service.logger, "error", messages.append)

    await operation_log_service.log_operation(
        FailingSession(),
        user_id=42,
        operation="登录",
    )

    assert len(messages) == 1
    assert "操作日志写入失败" in messages[0]
    assert "operation=登录" in messages[0]
    assert "user_id=42" in messages[0]
    assert "exception_type=RuntimeError" in messages[0]
