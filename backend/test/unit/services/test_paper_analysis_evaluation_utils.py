"""academic_paper_analysis_evaluation_service 纯函数单元测试。

不依赖外部服务，只验证盲审评分校验、运行结果序列化和持续时间计算逻辑。
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from yuxi.services.academic_paper_analysis_evaluation_service import (
    AcademicPaperAnalysisEvaluationError,
    RUBRIC_DIMENSIONS,
    AcademicPaperAnalysisEvaluationService,
    _duration_ms,
    _mean,
    _serialize_run_result,
)


# ---------------------------------------------------------------------------
# _validate_blind_scores
# ---------------------------------------------------------------------------

def _valid_blind_scores():
    return {
        "A": {dim: 4 for dim in RUBRIC_DIMENSIONS},
        "B": {dim: 5 for dim in RUBRIC_DIMENSIONS},
        "preference": "A",
    }


class TestValidateBlindScores:
    def test_valid_scores_pass(self):
        AcademicPaperAnalysisEvaluationService._validate_blind_scores(_valid_blind_scores())

    def test_missing_preference_raises_error(self):
        scores = _valid_blind_scores()
        del scores["preference"]
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"

    def test_invalid_preference_raises_error(self):
        scores = _valid_blind_scores()
        scores["preference"] = "C"
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"

    def test_preference_tie_accepted(self):
        scores = _valid_blind_scores()
        scores["preference"] = "tie"
        AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)

    def test_missing_dimension_raises_error(self):
        scores = _valid_blind_scores()
        del scores["A"]["evidence_faithfulness"]
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"

    def test_score_out_of_range_raises_error(self):
        scores = _valid_blind_scores()
        scores["A"]["coverage"] = 6
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"

    def test_score_zero_raises_error(self):
        scores = _valid_blind_scores()
        scores["B"]["coverage"] = 0
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"

    def test_boolean_score_rejected(self):
        scores = _valid_blind_scores()
        scores["A"]["coverage"] = True
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"

    def test_non_integer_score_rejected(self):
        scores = _valid_blind_scores()
        scores["A"]["coverage"] = 3.5
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"

    def test_missing_label_raises_error(self):
        scores = _valid_blind_scores()
        del scores["A"]
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"

    def test_extra_label_raises_error(self):
        scores = _valid_blind_scores()
        scores["C"] = {dim: 3 for dim in RUBRIC_DIMENSIONS}
        with pytest.raises(AcademicPaperAnalysisEvaluationError) as exc:
            AcademicPaperAnalysisEvaluationService._validate_blind_scores(scores)
        assert exc.value.error_type == "invalid_score"


# ---------------------------------------------------------------------------
# _serialize_run_result
# ---------------------------------------------------------------------------

class TestSerializeRunResult:
    def test_strips_strategy_model_paper_id(self):
        run = SimpleNamespace(
            result={
                "strategy": "multi_agent",
                "model": "deepseek:deepseek-chat",
                "paper_id": "p123",
                "structure": {"title": "Test"},
                "innovations": {"items": []},
            }
        )
        result = _serialize_run_result(run)
        assert "strategy" not in result
        assert "model" not in result
        assert "paper_id" not in result
        assert result["structure"]["title"] == "Test"
        assert "innovations" in result

    def test_empty_result_returns_empty_dict(self):
        run = SimpleNamespace(result={})
        assert _serialize_run_result(run) == {}

    def test_none_result_returns_empty_dict(self):
        run = SimpleNamespace(result=None)
        assert _serialize_run_result(run) == {}


# ---------------------------------------------------------------------------
# _duration_ms
# ---------------------------------------------------------------------------

class TestDurationMs:
    def test_valid_duration(self):
        run = SimpleNamespace(
            started_at=datetime(2026, 1, 1, 12, 0, 0),
            completed_at=datetime(2026, 1, 1, 12, 0, 5),
        )
        assert _duration_ms(run) == 5000

    def test_missing_started_at_returns_none(self):
        run = SimpleNamespace(started_at=None, completed_at=datetime(2026, 1, 1))
        assert _duration_ms(run) is None

    def test_missing_completed_at_returns_none(self):
        run = SimpleNamespace(started_at=datetime(2026, 1, 1), completed_at=None)
        assert _duration_ms(run) is None

    def test_zero_duration(self):
        ts = datetime(2026, 1, 1, 12, 0, 0)
        run = SimpleNamespace(started_at=ts, completed_at=ts)
        assert _duration_ms(run) == 0


# ---------------------------------------------------------------------------
# _mean
# ---------------------------------------------------------------------------

class TestMean:
    def test_basic_mean(self):
        assert _mean([1, 2, 3, 4, 5]) == 3.0

    def test_single_value(self):
        assert _mean([42]) == 42.0

    def test_empty_returns_none(self):
        assert _mean([]) is None

    def test_floats_rounded_to_2_decimals(self):
        assert _mean([1, 2]) == 1.5

    def test_mixed_int_float(self):
        assert _mean([1, 2.5]) == 1.75
