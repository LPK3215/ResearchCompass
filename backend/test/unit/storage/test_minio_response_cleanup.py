from __future__ import annotations

import pytest

from yuxi.storage.minio.client import MinIOClient, StorageError


class _Response:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.closed = False
        self.released = False

    def read(self):
        if self.error is not None:
            raise self.error
        return b"content"

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class _Minio:
    def __init__(self, response: _Response):
        self.response = response

    def get_object(self, **_kwargs):
        return self.response


def test_download_file_releases_response_after_success():
    response = _Response()
    client = MinIOClient()
    client._client = _Minio(response)

    assert client.download_file("bucket", "file") == b"content"
    assert response.closed is True
    assert response.released is True


def test_download_file_releases_response_after_read_failure():
    response = _Response(error=RuntimeError("read failed"))
    client = MinIOClient()
    client._client = _Minio(response)

    with pytest.raises(StorageError, match="下载文件失败") as exc_info:
        client.download_file("bucket", "file")

    assert response.closed is True
    assert response.released is True
    assert "read failed" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_async_download_file_releases_response_after_read_failure():
    response = _Response(error=RuntimeError("read failed"))
    client = MinIOClient()
    client._client = _Minio(response)

    with pytest.raises(StorageError, match="下载文件失败") as exc_info:
        await client.adownload_file("bucket", "file")

    assert response.closed is True
    assert response.released is True
    assert "read failed" not in str(exc_info.value)
