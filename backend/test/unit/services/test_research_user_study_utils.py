"""Unit tests for research_user_study_service pure utility functions."""

import pytest

from yuxi.services.research_user_study_service import (
    SUS_SCORE_KEYS,
    TASK_SCORE_KEYS,
    ResearchUserStudyError,
    _mean,
    _sus_score,
    _summary,
    _token_hash,
    _validate_score_map,
)


class TestSusScore:
    def test_all_max_scores(self):
        # SUS: odd questions contribute value-1, even questions contribute 5-value
        # All 5s: odd=4, even=0, sum=20, x2.5=50
        scores = {key: 5 for key in SUS_SCORE_KEYS}
        assert _sus_score(scores) == 50.0

    def test_all_min_scores(self):
        # All 1s: odd=0, even=4, sum=20, x2.5=50
        scores = {key: 1 for key in SUS_SCORE_KEYS}
        assert _sus_score(scores) == 50.0

    def test_perfect_scores(self):
        # Odd=5 (4), even=1 (4), sum=40, x2.5=100
        scores = {key: 5 if i % 2 else 1 for i, key in enumerate(SUS_SCORE_KEYS, start=1)}
        assert _sus_score(scores) == 100.0

    def test_worst_scores(self):
        # Odd=1 (0), even=5 (0), sum=0, x2.5=0
        scores = {key: 1 if i % 2 else 5 for i, key in enumerate(SUS_SCORE_KEYS, start=1)}
        assert _sus_score(scores) == 0.0

    def test_mixed_scores(self):
        scores = {key: 5 if i % 2 else 1 for i, key in enumerate(SUS_SCORE_KEYS, start=1)}
        assert _sus_score(scores) == 100.0

    def test_midpoint_scores(self):
        scores = {key: 3 for key in SUS_SCORE_KEYS}
        assert _sus_score(scores) == 50.0


class TestMean:
    def test_basic_mean(self):
        assert _mean([1, 2, 3, 4, 5]) == 3.0

    def test_single_value(self):
        assert _mean([7]) == 7.0

    def test_empty_returns_none(self):
        assert _mean([]) is None

    def test_floats(self):
        assert _mean([1.5, 2.5]) == 2.0

    def test_rounding(self):
        assert _mean([1, 2]) == 1.5
        assert _mean([1, 1, 1, 2]) == 1.25


class TestTokenHash:
    def test_consistent_hash(self):
        token = "abc123"
        h1 = _token_hash(token)
        h2 = _token_hash(token)
        assert h1 == h2

    def test_different_tokens_different_hash(self):
        assert _token_hash("token1") != _token_hash("token2")

    def test_hash_is_hex(self):
        h = _token_hash("test")
        assert len(h) == 64
        int(h, 16)


class TestValidateScoreMap:
    def test_valid_scores(self):
        values = {key: 3 for key in TASK_SCORE_KEYS}
        result = _validate_score_map(values, TASK_SCORE_KEYS, minimum=1, maximum=5, label="任务")
        assert result == values

    def test_missing_key(self):
        values = {key: 3 for key in TASK_SCORE_KEYS}
        del values[TASK_SCORE_KEYS[0]]
        with pytest.raises(ResearchUserStudyError) as exc:
            _validate_score_map(values, TASK_SCORE_KEYS, minimum=1, maximum=5, label="任务")
        assert exc.value.error_type == "invalid_response"

    def test_extra_key(self):
        values = {key: 3 for key in TASK_SCORE_KEYS}
        values["extra"] = 3
        with pytest.raises(ResearchUserStudyError):
            _validate_score_map(values, TASK_SCORE_KEYS, minimum=1, maximum=5, label="任务")

    def test_out_of_range(self):
        values = {key: 3 for key in TASK_SCORE_KEYS}
        values[TASK_SCORE_KEYS[0]] = 6
        with pytest.raises(ResearchUserStudyError):
            _validate_score_map(values, TASK_SCORE_KEYS, minimum=1, maximum=5, label="任务")

    def test_non_integer(self):
        values = {key: 3 for key in TASK_SCORE_KEYS}
        values[TASK_SCORE_KEYS[0]] = 3.5
        with pytest.raises(ResearchUserStudyError):
            _validate_score_map(values, TASK_SCORE_KEYS, minimum=1, maximum=5, label="任务")

    def test_boolean_rejected(self):
        values = {key: 3 for key in TASK_SCORE_KEYS}
        values[TASK_SCORE_KEYS[0]] = True
        with pytest.raises(ResearchUserStudyError):
            _validate_score_map(values, TASK_SCORE_KEYS, minimum=1, maximum=5, label="任务")


class TestSummary:
    def test_empty_responses(self):
        result = _summary([])
        assert result["response_count"] == 0
        assert result["sus_mean"] is None

    def test_single_response(self):
        class MockResponse:
            task_scores = {key: 4 for key in TASK_SCORE_KEYS}
            sus_scores = {key: 5 if i % 2 else 1 for i, key in enumerate(SUS_SCORE_KEYS, start=1)}
            overall_rating = 4
            recommend_score = 3
            research_stage = "literature_review"
            research_experience = "intermediate"

        result = _summary([MockResponse()])
        assert result["response_count"] == 1
        assert result["overall_rating_mean"] == 4.0
        assert result["sus_mean"] == 100.0
        assert result["research_stage_distribution"] == {"literature_review": 1}

    def test_multiple_responses(self):
        class MockResponse:
            def __init__(self, stage, rating):
                self.task_scores = {key: 3 for key in TASK_SCORE_KEYS}
                self.sus_scores = {key: 3 for key in SUS_SCORE_KEYS}
                self.overall_rating = rating
                self.recommend_score = 3
                self.research_stage = stage
                self.research_experience = "beginner"

        result = _summary(
            [
                MockResponse("lit_review", 4),
                MockResponse("data_analysis", 5),
            ]
        )
        assert result["response_count"] == 2
        assert result["overall_rating_mean"] == 4.5
        assert result["research_stage_distribution"] == {"lit_review": 1, "data_analysis": 1}

    def test_invalid_historical_response_fails_explicitly(self):
        class MockResponse:
            task_scores = {key: 3 for key in TASK_SCORE_KEYS if key != TASK_SCORE_KEYS[0]}
            sus_scores = {key: 3 for key in SUS_SCORE_KEYS}
            overall_rating = 4
            recommend_score = 3
            research_stage = "literature_review"
            research_experience = "intermediate"

        with pytest.raises(ResearchUserStudyError) as exc_info:
            _summary([MockResponse()])

        assert exc_info.value.error_type == "response_data_invalid"
