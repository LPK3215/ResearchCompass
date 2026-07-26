from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import chat_router


class _CapturingLogger:
    def __init__(self):
        self.entries: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, level):
        def record(*args, **kwargs):
            self.entries.append((level, args, kwargs))

        return record


@pytest.mark.asyncio
async def test_chat_call_does_not_expose_provider_failure(monkeypatch: pytest.MonkeyPatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    logger = _CapturingLogger()

    class Model:
        async def call(self, query):
            del query
            raise RuntimeError(secret)

    monkeypatch.setattr(chat_router, "select_model", lambda **_kwargs: Model())
    monkeypatch.setattr(chat_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await chat_router.call(
            query="question",
            meta={"request_id": "req-1"},
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "模型调用失败，请稍后重试"
    assert secret not in repr(logger.entries)


@pytest.mark.asyncio
async def test_thread_history_preserves_access_error(monkeypatch: pytest.MonkeyPatch):
    async def fail(**_kwargs):
        raise HTTPException(status_code=404, detail="对话线程不存在")

    monkeypatch.setattr(chat_router, "get_thread_history_view", fail)

    with pytest.raises(HTTPException) as exc_info:
        await chat_router.get_thread_history(
            thread_id="thread-1",
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "对话线程不存在"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "expected_detail"),
    [
        ("get_thread_history", "获取对话历史消息失败"),
        ("get_thread_state", "获取对话状态失败"),
    ],
)
async def test_thread_read_failure_does_not_expose_internal_error(
    monkeypatch: pytest.MonkeyPatch,
    handler_name: str,
    expected_detail: str,
):
    secret = "Authorization=secret-api-key provider-body=<private>"
    logger = _CapturingLogger()

    async def fail(**_kwargs):
        raise RuntimeError(secret)

    target = chat_router.get_thread_history if handler_name == "get_thread_history" else chat_router.get_thread_state
    dependency = "get_thread_history_view" if handler_name == "get_thread_history" else "get_agent_state_view"
    monkeypatch.setattr(chat_router, dependency, fail)
    monkeypatch.setattr(chat_router, "logger", logger)

    kwargs = {
        "thread_id": "thread-1",
        "current_user": SimpleNamespace(uid="user-1"),
        "db": object(),
    }
    if handler_name == "get_thread_state":
        kwargs["include_messages"] = False

    with pytest.raises(HTTPException) as exc_info:
        await target(**kwargs)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == expected_detail
    assert secret not in repr(logger.entries)


@pytest.mark.asyncio
async def test_image_processor_failure_does_not_expose_internal_error(monkeypatch: pytest.MonkeyPatch):
    secret = "Authorization=secret-api-key provider-body=<private>"

    class Upload:
        content_type = "image/png"
        filename = "image.png"

        async def read(self):
            return b"image"

    monkeypatch.setattr(
        chat_router,
        "process_uploaded_image",
        lambda *_args: {"success": False, "error": secret},
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_router.upload_image(file=Upload(), current_user=SimpleNamespace(id=1))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "图片处理失败，请检查文件格式"
    assert secret not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_image_upload_unknown_failure_does_not_expose_internal_error(monkeypatch: pytest.MonkeyPatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    logger = _CapturingLogger()

    class Upload:
        content_type = "image/png"
        filename = "image.png"

        async def read(self):
            raise RuntimeError(secret)

    monkeypatch.setattr(chat_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await chat_router.upload_image(file=Upload(), current_user=SimpleNamespace(id=1))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "图片处理失败，请稍后重试"
    assert secret not in repr(logger.entries)
