from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from yuxi.knowledge.parser import zip_utils


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


@pytest.mark.asyncio
async def test_process_zip_rejects_oversized_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    zip_path = tmp_path / "oversized.zip"
    _write_zip(zip_path, {"full.md": b"12345"})
    monkeypatch.setattr(zip_utils, "MAX_ZIP_ENTRY_BYTES", 4)

    with pytest.raises(ValueError, match="条目超过大小限制"):
        await zip_utils.process_zip_file(str(zip_path))


@pytest.mark.asyncio
async def test_process_zip_rejects_backslash_path_traversal(tmp_path: Path):
    zip_path = tmp_path / "traversal.zip"
    _write_zip(zip_path, {"..\\full.md": b"unsafe"})

    with pytest.raises(ValueError, match="不安全路径"):
        await zip_utils.process_zip_file(str(zip_path))


@pytest.mark.asyncio
async def test_process_zip_rolls_back_uploaded_images_after_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    zip_path = tmp_path / "images.zip"
    _write_zip(
        zip_path,
        {
            "full.md": b"![one](images/one.png)\n![two](images/two.png)",
            "images/one.png": b"one",
            "images/two.png": b"two",
        },
    )
    uploaded: list[str] = []
    deleted: list[str] = []
    messages: list[str] = []

    class FakeMinioClient:
        def ensure_bucket_exists(self, bucket_name: str) -> None:
            assert bucket_name == "public"

        async def aupload_file(self, *, bucket_name: str, object_name: str, data: bytes):
            if uploaded:
                raise RuntimeError(secret)
            uploaded.append(object_name)
            return SimpleNamespace(url=f"minio://{bucket_name}/{object_name}")

        async def adelete_file(self, bucket_name: str, object_name: str) -> None:
            deleted.append(object_name)

    class CapturedLogger:
        def debug(self, message: str) -> None:
            messages.append(message)

        def warning(self, message: str) -> None:
            messages.append(message)

        def error(self, message: str) -> None:
            messages.append(message)

    timestamps = iter([1.0, 2.0])
    monkeypatch.setattr(zip_utils, "get_minio_client", FakeMinioClient)
    monkeypatch.setattr(zip_utils.time, "time", lambda: next(timestamps))
    monkeypatch.setattr(zip_utils, "logger", CapturedLogger())

    with pytest.raises(RuntimeError, match="ZIP 图片处理失败") as exc_info:
        await zip_utils.process_zip_file(str(zip_path), image_prefix="kb/images")

    assert deleted == uploaded
    assert secret not in str(exc_info.value)
    assert secret not in " ".join(messages)
