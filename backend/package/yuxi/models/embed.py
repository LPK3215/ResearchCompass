import asyncio
import math
import os
import time
from abc import ABC, abstractmethod

import httpx
import numpy as np
import requests

from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url, hashstr, logger

EMBEDDING_RATE_LIMIT_MAX_RETRIES = 10
EMBEDDING_TRANSIENT_MAX_RETRIES = 2
EMBEDDING_RETRY_MAX_DELAY_SECONDS = 10.0
EMBEDDING_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


class BaseEmbeddingModel(ABC):
    def __init__(
        self,
        model=None,
        name=None,
        dimension=None,
        url=None,
        base_url=None,
        api_key=None,
        model_id=None,
        batch_size=40,
    ):
        base_url = base_url or url
        self.model = model or name or model_id
        self.dimension = dimension
        self.base_url = get_docker_safe_url(base_url)
        self.api_key = os.getenv(api_key, api_key)
        self.batch_size = int(batch_size or 40)
        self.embed_state = {}

    @abstractmethod
    def encode(self, message: list[str] | str) -> list[list[float]]:
        raise NotImplementedError("Subclasses must implement this method")

    def encode_queries(self, queries: list[str] | str) -> list[list[float]]:
        return self.encode(queries)

    @abstractmethod
    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        raise NotImplementedError("Subclasses must implement this method")

    async def aencode_queries(self, queries: list[str] | str) -> list[list[float]]:
        return await self.aencode(queries)

    def batch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        batch_size = batch_size or self.batch_size
        data = []
        task_id = None
        if len(messages) > batch_size:
            task_id = hashstr(messages)
            self.embed_state[task_id] = {"status": "in-progress", "total": len(messages), "progress": 0}

        try:
            for i in range(0, len(messages), batch_size):
                group_msg = messages[i : i + batch_size]
                logger.info(f"Encoding [{i}/{len(messages)}] messages (bsz={batch_size})")
                response = self.encode(group_msg)
                data.extend(response)
                if task_id:
                    self.embed_state[task_id]["progress"] = i + len(group_msg)
            return data
        finally:
            if task_id:
                self.embed_state.pop(task_id, None)

    async def abatch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        batch_size = batch_size or self.batch_size
        data = []
        task_id = None
        if len(messages) > batch_size:
            task_id = hashstr(messages)
            self.embed_state[task_id] = {"status": "in-progress", "total": len(messages), "progress": 0}

        try:
            for i in range(0, len(messages), batch_size):
                group_msg = messages[i : i + batch_size]
                logger.info(f"Async encoding [{i}/{len(messages)}] messages (bsz={batch_size})")
                res = await self.aencode(group_msg)
                data.extend(res)
                if task_id:
                    self.embed_state[task_id]["progress"] = i + len(group_msg)
            return data
        finally:
            if task_id:
                self.embed_state.pop(task_id, None)

    async def test_connection(self) -> tuple[bool, str]:
        try:
            embeddings = await self.aencode(["Hello world"])
            if self.dimension not in (None, ""):
                actual_dimension = len(embeddings[0]) if embeddings else 0
                expected_dimension = int(self.dimension)
                if actual_dimension != expected_dimension:
                    return False, f"Embedding 维度不一致：配置 {expected_dimension}，实际 {actual_dimension}"
            return True, "连接正常"
        except Exception as exc:
            logger.error("Embedding connection test failed: exception_type={}", type(exc).__name__)
            return False, "连接失败"


class OtherEmbedding(BaseEmbeddingModel):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def build_payload(self, message: list[str] | str) -> dict:
        return {"model": self.model, "input": message}

    @staticmethod
    def _retry_delay_seconds(retry_index: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                delay = float(retry_after)
                if math.isfinite(delay) and delay > 0:
                    return min(delay, EMBEDDING_RETRY_MAX_DELAY_SECONDS)
            except (TypeError, ValueError):
                pass
        return min(float(2 ** (retry_index - 1)), EMBEDDING_RETRY_MAX_DELAY_SECONDS)

    def _prepare_retry(
        self,
        message: list[str] | str,
        *,
        retry_index: int,
        response=None,
        error: Exception | None = None,
    ) -> tuple[int, float] | None:
        status_code = getattr(response, "status_code", None)
        messages = [message] if isinstance(message, str) else message

        if status_code == 400 and response is not None:
            logger.warning(
                "Embedding request returned 400 Bad Request: "
                f"model={self.model}, input_count={len(messages)}, "
                f"input_lengths={[len(item) for item in messages]}"
            )

        if status_code == 429:
            max_retries = EMBEDDING_RATE_LIMIT_MAX_RETRIES
        elif status_code in EMBEDDING_RETRYABLE_STATUS_CODES or status_code is None:
            max_retries = EMBEDDING_TRANSIENT_MAX_RETRIES
        else:
            max_retries = 0
        if retry_index >= max_retries:
            return None

        next_retry_index = retry_index + 1
        retry_after = response.headers.get("Retry-After") if response is not None else None
        delay = self._retry_delay_seconds(next_retry_index, retry_after)
        reason = f"status={status_code}" if status_code is not None else f"error={type(error).__name__}"
        logger.warning(
            "Retrying embedding request: "
            f"{reason}, model={self.model}, "
            f"retry={next_retry_index}/{max_retries}, delay={delay:.1f}s, "
            f"input_count={len(messages)}"
        )
        return next_retry_index, delay

    @staticmethod
    def _extract_embeddings(result: dict) -> list[list[float]]:
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise ValueError("Embedding response must contain a data list")
        embeddings = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list):
                raise ValueError("Embedding response item is invalid")
            embeddings.append(embedding)
        return embeddings

    def encode(self, message: list[str] | str) -> list[list[float]]:
        payload = self.build_payload(message)
        retry_index = 0
        with requests.Session() as session:
            session.trust_env = False
            while True:
                try:
                    response = session.post(self.base_url, json=payload, headers=self.headers, timeout=60)
                    response.raise_for_status()
                    return self._extract_embeddings(response.json())
                except requests.RequestException as exc:
                    retry = self._prepare_retry(
                        message,
                        retry_index=retry_index,
                        response=getattr(exc, "response", None),
                        error=exc,
                    )
                    if retry:
                        retry_index, delay = retry
                        time.sleep(delay)
                        continue

                    messages = [message] if isinstance(message, str) else message
                    logger.error(
                        "Embedding request failed: {}, model={}, input_count={}",
                        type(exc).__name__,
                        self.model,
                        len(messages),
                    )
                    raise ValueError("Embedding request failed") from exc

    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        payload = self.build_payload(message)
        async with httpx.AsyncClient(trust_env=False) as client:
            retry_index = 0
            while True:
                try:
                    response = await client.post(self.base_url, json=payload, headers=self.headers, timeout=60)
                    response.raise_for_status()
                    return self._extract_embeddings(response.json())
                except httpx.HTTPStatusError as exc:
                    retry = self._prepare_retry(
                        message,
                        retry_index=retry_index,
                        response=exc.response,
                        error=exc,
                    )
                    if retry:
                        retry_index, delay = retry
                        await asyncio.sleep(delay)
                        continue
                    raise ValueError("Embedding request failed") from exc
                except httpx.RequestError as exc:
                    retry = self._prepare_retry(message, retry_index=retry_index, error=exc)
                    if retry:
                        retry_index, delay = retry
                        await asyncio.sleep(delay)
                        continue
                    raise ValueError("Embedding request failed") from exc


def get_embedding_model_info_by_id(model_id: str) -> dict:
    info = model_cache.get_model_info(model_id)
    if not info:
        raise ValueError(f"Unknown embedding model spec: {model_id}")
    if info.model_type != "embedding":
        raise ValueError(f"Model {model_id} is not an embedding model (type={info.model_type})")

    logger.info(f"Loaded embedding model info for {model_id}")
    return {
        "name": info.model_id,
        "display_name": info.display_name,
        "dimension": info.dimension,
        "base_url": info.base_url,
        "api_key": info.api_key,
        "model_id": info.spec,
        "batch_size": info.batch_size,
    }


def select_embedding_model(model_id: str):
    info = model_cache.get_model_info(model_id)
    if not info:
        raise ValueError(f"Unknown embedding model spec: {model_id}")

    if info.model_type != "embedding":
        raise ValueError(f"Model {model_id} is not an embedding model (type={info.model_type})")

    logger.info(f"Selecting embedding model: {model_id} (provider_type={info.provider_type})")
    return OtherEmbedding(
        model=info.model_id,
        base_url=info.base_url,
        api_key=info.api_key,
        dimension=info.dimension,
        batch_size=info.batch_size,
    )


async def test_embedding_model_status_by_spec(spec: str) -> dict:
    try:
        model = select_embedding_model(spec)
        success, message = await model.test_connection()
        return {
            "spec": spec,
            "status": "available" if success else "unavailable",
            "message": "连接正常" if success else message,
        }
    except Exception as e:
        logger.warning(f"测试 Embedding 模型状态失败 {spec}: {e}")
        return {"spec": spec, "status": "error", "message": str(e)}
