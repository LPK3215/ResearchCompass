from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.services import conversation_service as cs


class _DummyUpload:
    def __init__(self, *, filename: str, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._buffer = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    async def seek(self, offset: int) -> int:
        return self._buffer.seek(offset)


def test_build_attachment_storage_path_uses_thread_local_uploads_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path))

    virtual_path, host_path = cs._build_attachment_storage_path(
        uid="u-1",
        thread_id="t-1",
        file_name="demo.txt",
    )

    assert virtual_path == "/home/gem/user-data/uploads/attachments/demo.md"
    assert host_path == tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "attachments" / "demo.md"


def test_serialize_attachment_includes_original_file_fields() -> None:
    serialized = cs.serialize_attachment(
        {
            "file_id": "f-1",
            "file_name": "demo.txt",
            "file_type": "text/plain",
            "file_size": 5,
            "status": "parsed",
            "uploaded_at": "2026-03-25T00:00:00+00:00",
            "path": "/home/gem/user-data/uploads/attachments/demo.md",
            "artifact_url": "/api/chat/thread/t-1/artifacts/home/gem/user-data/uploads/attachments/demo.md",
            "original_path": "/home/gem/user-data/uploads/demo.txt",
            "original_artifact_url": "/api/chat/thread/t-1/artifacts/home/gem/user-data/uploads/demo.txt",
            "minio_url": None,
        }
    )

    assert serialized["path"] == "/home/gem/user-data/uploads/attachments/demo.md"
    assert serialized["original_path"] == "/home/gem/user-data/uploads/demo.txt"
    assert serialized["original_artifact_url"] == "/api/chat/thread/t-1/artifacts/home/gem/user-data/uploads/demo.txt"


@pytest.mark.asyncio
async def test_materialize_attachment_files_keeps_original_file_when_markdown_conversion_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path))

    upload = _DummyUpload(filename="demo.pdf", content_type="application/pdf", data=b"%PDF-test")

    result = await cs._materialize_attachment_files(
        thread_id="t-1",
        uid="u-1",
        upload=upload,
        file_name="demo.pdf",
        file_content=b"%PDF-test",
    )

    assert result["status"] == "uploaded"
    assert result["path"] == "/home/gem/user-data/uploads/demo.pdf"
    assert result["original_path"] == "/home/gem/user-data/uploads/demo.pdf"
    assert (tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "demo.pdf").read_bytes() == b"%PDF-test"


@pytest.mark.asyncio
async def test_materialize_attachment_files_writes_markdown_copy_when_conversion_succeeds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path))

    async def _fake_convert(_upload):
        return cs.ConversionResult(
            file_id="f-1",
            file_name="demo.txt",
            file_type="text/plain",
            file_size=5,
            markdown="hello\nworld",
            truncated=False,
        )

    monkeypatch.setattr(cs, "_convert_upload_to_markdown", _fake_convert)

    upload = _DummyUpload(filename="demo.txt", content_type="text/plain", data=b"hello")

    result = await cs._materialize_attachment_files(
        thread_id="t-1",
        uid="u-1",
        upload=upload,
        file_name="demo.txt",
        file_content=b"hello",
    )

    assert result["status"] == "parsed"
    assert result["path"] == "/home/gem/user-data/uploads/attachments/demo.md"
    assert result["original_path"] == "/home/gem/user-data/uploads/demo.txt"
    assert result["file_path"] == "/home/gem/user-data/uploads/attachments/demo.md"
    assert result["markdown"] == "hello\nworld"
    assert (tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "demo.txt").read_bytes() == b"hello"
    assert (tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "attachments" / "demo.md").read_text(
        encoding="utf-8"
    ) == "hello\nworld"


@pytest.mark.asyncio
async def test_upload_thread_attachment_uses_unique_storage_names_for_duplicate_filenames(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path))
    attachments: list[dict] = []
    conversation = SimpleNamespace(id=1, uid="u-1", agent_id="agent", extra_metadata={}, status="active")

    class FakeConversationRepository:
        def __init__(self, db):
            del db

        async def get_conversation_by_thread_id(self, thread_id):
            return conversation

        async def add_attachment(self, conversation_id, attachment):
            attachments.append(attachment)

        async def get_attachments(self, conversation_id):
            return list(attachments)

    async def unsupported_conversion(upload):
        raise ValueError("unsupported")

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(cs, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(cs, "_convert_upload_to_markdown", unsupported_conversion)
    monkeypatch.setattr(cs, "_sync_thread_upload_state", no_op)
    monkeypatch.setattr(cs, "invalidate_mention_cache", no_op)

    first = await cs.upload_thread_attachment_view(
        thread_id="t-1",
        file=_DummyUpload(filename="demo.txt", content_type="text/plain", data=b"first"),
        db=object(),
        current_uid="u-1",
    )
    second = await cs.upload_thread_attachment_view(
        thread_id="t-1",
        file=_DummyUpload(filename="demo.txt", content_type="text/plain", data=b"second"),
        db=object(),
        current_uid="u-1",
    )

    assert first["path"] != second["path"]
    assert Path(attachments[0]["storage_path"]).read_bytes() == b"first"
    assert Path(attachments[1]["storage_path"]).read_bytes() == b"second"


@pytest.mark.asyncio
async def test_delete_thread_attachment_refuses_host_path_outside_thread_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path / "saves"))
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("keep", encoding="utf-8")
    warnings: list[str] = []
    conversation = SimpleNamespace(id=1, uid="u-1", agent_id="agent", extra_metadata={}, status="active")

    class FakeConversationRepository:
        def __init__(self, db):
            del db
            self.removed = False

        async def get_conversation_by_thread_id(self, thread_id):
            return conversation

        async def get_attachments(self, conversation_id):
            if self.removed:
                return []
            return [{"file_id": "f-1", "storage_path": str(outside_file)}]

        async def remove_attachment(self, conversation_id, file_id):
            self.removed = True
            return True

    async def no_op(*args, **kwargs):
        return None

    repository = FakeConversationRepository(None)
    monkeypatch.setattr(cs, "ConversationRepository", lambda db: repository)
    monkeypatch.setattr(cs, "_sync_thread_upload_state", no_op)
    monkeypatch.setattr(cs, "invalidate_mention_cache", no_op)
    monkeypatch.setattr(cs.logger, "warning", warnings.append)

    await cs.delete_thread_attachment_view(
        thread_id="t-1",
        file_id="f-1",
        db=object(),
        current_uid="u-1",
    )

    assert outside_file.read_text(encoding="utf-8") == "keep"
    assert any("outside thread user-data" in message for message in warnings)
