from __future__ import annotations

import pytest

from yuxi.knowledge.implementations.dify import DifyAPIError, DifyKB


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    last_init_kwargs: dict = {}

    def __init__(self, response_payload: object | None = None, raises: Exception | None = None, **kwargs):
        type(self).last_init_kwargs = kwargs
        self._response_payload = {} if response_payload is None else response_payload
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def post(self, url: str, json: dict, headers: dict):
        assert "/datasets/" in url
        assert headers.get("Authorization", "").startswith("Bearer ")
        if self._raises:
            raise self._raises
        if "retrieval_model" in json:
            assert json["retrieval_model"]["search_method"] == "semantic_search"
            assert json["retrieval_model"]["top_k"] == 5
            assert json["retrieval_model"]["reranking_enable"] is False
            assert json["retrieval_model"]["score_threshold_enabled"] is True
            assert json["retrieval_model"]["score_threshold"] == 0.3
        return _FakeResponse(self._response_payload)


def test_dify_create_params_config_and_validation():
    config = DifyKB.get_create_params_config()
    keys = [option["key"] for option in config["options"]]
    assert keys == ["dify_api_url", "dify_token", "dify_dataset_id"]
    assert all(option["required"] for option in config["options"])

    params = DifyKB.normalize_additional_params(
        {
            "dify_api_url": " https://api.dify.ai/v1 ",
            "dify_token": " token ",
            "dify_dataset_id": " dataset-123 ",
        }
    )
    assert params == {
        "dify_api_url": "https://api.dify.ai/v1",
        "dify_token": "token",
        "dify_dataset_id": "dataset-123",
    }
    assert "chunk_preset_id" not in params


def test_dify_validation_rejects_missing_or_invalid_params():
    with pytest.raises(ValueError, match="Dify 参数缺失"):
        DifyKB.normalize_additional_params({"dify_api_url": "https://api.dify.ai/v1"})

    with pytest.raises(ValueError, match="必须以 /v1 结尾"):
        DifyKB.normalize_additional_params(
            {
                "dify_api_url": "https://api.dify.ai",
                "dify_token": "token",
                "dify_dataset_id": "dataset-123",
            }
        )


@pytest.mark.asyncio
async def test_dify_kb_aquery_maps_records(monkeypatch, tmp_path):
    kb = DifyKB(str(tmp_path))
    slug = "kb_test_dify"
    kb.databases_meta[slug] = {
        "name": "dify-kb",
        "description": "test",
        "kb_type": "dify",
        "query_params": {
            "options": {
                "search_mode": "vector",
                "final_top_k": 5,
                "score_threshold_enabled": True,
                "similarity_threshold": 0.3,
            }
        },
        "metadata": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "token",
            "dify_dataset_id": "dataset-123",
        },
    }

    payload = {
        "records": [
            {
                "score": 0.98,
                "segment": {
                    "id": "seg-1",
                    "position": 2,
                    "content": "hello world",
                    "document": {"id": "doc-1", "name": "Doc One"},
                },
            }
        ]
    }

    monkeypatch.setattr(
        "yuxi.knowledge.implementations.dify.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response_payload=payload, **kwargs),
    )

    result = await kb.aquery("hello", slug)
    assert _FakeAsyncClient.last_init_kwargs["trust_env"] is False
    assert len(result) == 1
    assert result[0]["content"] == "hello world"
    assert result[0]["score"] == 0.98
    assert result[0]["metadata"]["source"] == "Doc One"
    assert result[0]["metadata"]["file_id"] == "doc-1"
    assert result[0]["metadata"]["chunk_id"] == "seg-1"
    assert result[0]["metadata"]["chunk_index"] == 2


@pytest.mark.asyncio
async def test_dify_kb_aquery_error_is_explicit_and_sanitized(monkeypatch, tmp_path):
    kb = DifyKB(str(tmp_path))
    slug = "kb_test_dify_error"
    kb.databases_meta[slug] = {
        "name": "dify-kb",
        "description": "test",
        "kb_type": "dify",
        "query_params": {"options": {}},
        "metadata": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "token",
            "dify_dataset_id": "dataset-123",
        },
    }

    secret = "Authorization=secret-api-key provider-body=<private>"
    messages: list[str] = []

    class _CapturedLogger:
        def error(self, message: str) -> None:
            messages.append(message)

        def warning(self, message: str) -> None:
            messages.append(message)

    monkeypatch.setattr("yuxi.knowledge.implementations.dify.logger", _CapturedLogger())
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.dify.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(raises=RuntimeError(secret), **kwargs),
    )

    with pytest.raises(DifyAPIError, match="^Dify 知识库查询失败$") as exc_info:
        await kb.aquery("hello", slug)

    assert secret not in str(exc_info.value)
    assert secret not in " ".join(messages)


@pytest.mark.asyncio
async def test_dify_kb_retries_with_query_only_payload(monkeypatch, tmp_path):
    kb = DifyKB(str(tmp_path))
    slug = "kb_test_dify_fallback"
    kb.databases_meta[slug] = {
        "query_params": {"options": {}},
        "metadata": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "token",
            "dify_dataset_id": "dataset-123",
        },
    }
    payloads: list[dict] = []

    class _FallbackClient(_FakeAsyncClient):
        async def post(self, url: str, json: dict, headers: dict):
            del url, headers
            payloads.append(json)
            if len(payloads) == 1:
                raise RuntimeError("unsupported retrieval model")
            return _FakeResponse({"records": []})

    monkeypatch.setattr(
        "yuxi.knowledge.implementations.dify.httpx.AsyncClient",
        lambda **kwargs: _FallbackClient(**kwargs),
    )

    assert await kb.aquery("hello", slug) == []
    assert "retrieval_model" in payloads[0]
    assert payloads[1] == {"query": "hello"}
    assert _FallbackClient.last_init_kwargs["trust_env"] is False


@pytest.mark.asyncio
async def test_dify_request_rejects_non_object_json(monkeypatch, tmp_path):
    kb = DifyKB(str(tmp_path))
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.dify.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response_payload=[], **kwargs),
    )

    with pytest.raises(DifyAPIError, match="^Dify 知识库响应格式无效$"):
        await kb._request_dify(
            client_payload={"query": "hello"},
            request_url="https://api.dify.ai/v1/datasets/dataset-123/retrieve",
            headers={"Authorization": "Bearer token"},
        )
