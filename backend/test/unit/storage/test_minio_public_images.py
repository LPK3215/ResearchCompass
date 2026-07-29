import json

import pytest

from yuxi.storage.minio.client import MinIOClient, normalize_public_minio_url


class FakeMinio:
    def __init__(self):
        self.policy = None

    def bucket_exists(self, bucket_name: str) -> bool:
        return False

    def make_bucket(self, bucket_name: str) -> None:
        return None

    def set_bucket_policy(self, bucket_name: str, policy: str) -> None:
        self.policy = json.loads(policy)

    def put_object(self, **kwargs):
        return object()


def test_public_image_uses_same_origin_url_without_bucket_listing(monkeypatch):
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")
    client = MinIOClient()
    fake_minio = FakeMinio()
    client._client = fake_minio

    result = client.upload_file("public", "images/user 1/avatar.png", b"image", "image/png")

    assert result.url == "/minio/public/images/user%201/avatar.png"
    assert fake_minio.policy is not None
    actions = [action for statement in fake_minio.policy["Statement"] for action in statement["Action"]]
    assert actions == ["s3:GetObject"]


def test_legacy_public_minio_url_is_normalized_to_same_origin(monkeypatch):
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")

    assert (
        normalize_public_minio_url("http://example.test:9000/public/avatar/user.png") == "/minio/public/avatar/user.png"
    )
    assert normalize_public_minio_url("https://cdn.example.test/public/user.png") == (
        "https://cdn.example.test/public/user.png"
    )


def test_legacy_public_minio_url_preserves_query_and_fragment(monkeypatch):
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")

    assert (
        normalize_public_minio_url("http://example.test:9000/public/avatar/user.png?v=123#preview")
        == "/minio/public/avatar/user.png?v=123#preview"
    )


@pytest.mark.asyncio
async def test_delete_prefix_treats_missing_bucket_as_empty_without_listing():
    class MissingBucketMinio:
        def bucket_exists(self, bucket_name):
            return False

        def list_objects(self, *args, **kwargs):
            raise AssertionError("missing bucket must not be listed")

    client = MinIOClient()
    client._client = MissingBucketMinio()

    assert await client.adelete_objects_by_prefix("public", "kb-1/") == 0


@pytest.mark.asyncio
async def test_delete_prefix_keeps_real_storage_failures_visible():
    class FailingMinio:
        def bucket_exists(self, bucket_name):
            raise RuntimeError("storage unavailable")

    client = MinIOClient()
    client._client = FailingMinio()

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await client.adelete_objects_by_prefix("public", "kb-1/")
