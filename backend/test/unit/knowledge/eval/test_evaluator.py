import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest

from yuxi.knowledge.eval.evaluator import (
    EvaluationAnswerGenerationError,
    aggregate_metrics,
    build_answer_prompt,
    generate_answer_if_needed,
    normalize_query_result,
)


def test_normalize_query_result_supports_dict_and_list():
    answer, chunks = normalize_query_result({"answer": "A", "retrieved_chunks": [{"content": "C"}]})
    assert answer == "A"
    assert chunks == [{"content": "C"}]

    answer, chunks = normalize_query_result([{"content": "C"}])
    assert answer == ""
    assert chunks == [{"content": "C"}]


def test_build_answer_prompt_uses_first_five_non_empty_chunks():
    chunks = [{"content": f"内容{i}"} for i in range(6)] + [{"content": ""}]

    prompt = build_answer_prompt("问题", chunks)

    assert "用户问题：问题" in prompt
    assert "内容0" in prompt
    assert "内容4" in prompt
    assert "内容5" not in prompt


def test_aggregate_metrics_matches_service_output_shape():
    metrics, overall_score = aggregate_metrics(
        [{"recall@1": 1.0, "f1@1": 0.0}, {"recall@1": 0.0, "f1@1": 1.0}],
        [{"score": 1.0}, {"score": 0.0}],
        include_overall_score=True,
    )

    assert metrics["recall@1"] == 0.5
    assert metrics["f1@1"] == 0.5
    assert metrics["answer_correctness"] == 0.5
    assert metrics["overall_score"] == overall_score


@pytest.mark.asyncio
async def test_answer_generation_failure_does_not_expose_provider_secret():
    secret = "Authorization=secret-api-key provider-body=<private>"

    class Model:
        async def call(self, *args, **kwargs):
            raise RuntimeError(secret)

    with pytest.raises(EvaluationAnswerGenerationError, match="评估答案模型调用失败") as exc_info:
        await generate_answer_if_needed(
            query="question",
            generated_answer="",
            retrieved_chunks=[{"content": "evidence"}],
            retrieval_config={"answer_llm": "provider:model"},
            select_model_fn=lambda **kwargs: Model(),
        )

    assert secret not in str(exc_info.value)
