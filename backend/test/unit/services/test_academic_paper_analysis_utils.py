"""academic_paper_analysis_service 纯函数单元测试。

不依赖外部 LLM 或数据库，只验证 JSON 解析和阶段校验逻辑。
"""

from __future__ import annotations

import pytest

from yuxi.services.academic_paper_analysis_service import (
    AcademicPaperAnalysisError,
    STAGE_INSTRUCTIONS,
    _ensure_analysis_context_budget,
    _parse_json,
    _validate_stage,
)


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------


class TestParseJson:
    def test_plain_json_object_returns_dict(self):
        result = _parse_json('{"key": "value"}', stage="structure")
        assert result == {"key": "value"}

    def test_json_in_markdown_code_block_stripped(self):
        result = _parse_json('```json\n{"key": "value"}\n```', stage="structure")
        assert result == {"key": "value"}

    def test_json_in_plain_code_block_stripped(self):
        result = _parse_json('```\n{"key": "value"}\n```', stage="structure")
        assert result == {"key": "value"}

    def test_invalid_json_raises_error(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _parse_json("not json", stage="structure")
        assert exc_info.value.error_type == "analysis_invalid_json"
        assert "structure" in exc_info.value.message

    def test_json_array_raises_schema_error(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _parse_json('["a", "b"]', stage="structure")
        assert exc_info.value.error_type == "analysis_invalid_schema"

    def test_json_string_raises_schema_error(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _parse_json('"just a string"', stage="structure")
        assert exc_info.value.error_type == "analysis_invalid_schema"

    def test_empty_string_raises_json_error(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _parse_json("", stage="innovations")
        assert exc_info.value.error_type == "analysis_invalid_json"

    def test_stage_name_included_in_error_message(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _parse_json("invalid", stage="methodology")
        assert "methodology" in exc_info.value.message


# ---------------------------------------------------------------------------
# _validate_stage - structure
# ---------------------------------------------------------------------------


class TestValidateStageStructure:
    REQUIRED_FIELDS = {"title", "authors", "problem", "method", "datasets", "results", "limitations"}

    def test_valid_structure_passes(self):
        payload = {
            "title": "Test Paper",
            "authors": ["Alice"],
            "problem": "Problem desc",
            "method": "Method desc",
            "datasets": ["Dataset1"],
            "results": ["Result1"],
            "limitations": ["Limitation1"],
        }
        result = _validate_stage("structure", payload)
        assert result is payload

    def test_missing_field_raises_error_with_field_name(self):
        payload = {
            "title": "Test",
            "authors": [],
            "problem": "p",
            "method": "m",
            "datasets": [],
            "results": [],
            # limitations missing
        }
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("structure", payload)
        assert exc_info.value.error_type == "analysis_invalid_schema"
        assert "limitations" in exc_info.value.message

    def test_multiple_missing_fields_listed_alphabetically(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("structure", {"title": "T"})
        msg = exc_info.value.message
        # fields should be listed in alphabetical order
        fields_in_msg = ["authors", "datasets", "limitations", "method", "problem", "results"]
        for f in fields_in_msg:
            assert f in msg


# ---------------------------------------------------------------------------
# _validate_stage - innovations
# ---------------------------------------------------------------------------


class TestValidateStageInnovations:
    def test_valid_innovations_with_3_items_passes(self):
        payload = {
            "items": [
                {"claim": "c1", "evidence": "e1", "confidence": "high"},
                {"claim": "c2", "evidence": "e2", "confidence": "medium"},
                {"claim": "c3", "evidence": "e3", "confidence": "low"},
            ]
        }
        result = _validate_stage("innovations", payload)
        assert result is payload

    def test_single_item_passes(self):
        payload = {"items": [{"claim": "c1", "evidence": "e1"}]}
        result = _validate_stage("innovations", payload)
        assert result is payload

    def test_empty_items_raises_error(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("innovations", {"items": []})
        assert "1 到 10" in exc_info.value.message

    def test_eleven_items_raises_error(self):
        payload = {"items": [{"claim": f"c{i}"} for i in range(11)]}
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("innovations", payload)
        assert "1 到 10" in exc_info.value.message

    def test_items_not_list_raises_error(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("innovations", {"items": "not a list"})
        assert "1 到 10" in exc_info.value.message

    def test_missing_items_raises_error(self):
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("innovations", {})
        assert "items" in exc_info.value.message


# ---------------------------------------------------------------------------
# _validate_stage - methodology
# ---------------------------------------------------------------------------


class TestValidateStageMethodology:
    def test_valid_methodology_passes(self):
        payload = {
            "research_design": "Experimental",
            "method_steps": ["step1", "step2"],
            "evaluation": ["metric1"],
            "reproducibility": {"available": True, "details": ["code released"]},
        }
        result = _validate_stage("methodology", payload)
        assert result is payload

    def test_missing_reproducibility_raises_error(self):
        payload = {
            "research_design": "Experimental",
            "method_steps": [],
            "evaluation": [],
        }
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("methodology", payload)
        assert "reproducibility" in exc_info.value.message


# ---------------------------------------------------------------------------
# _validate_stage - gaps
# ---------------------------------------------------------------------------


class TestValidateStageGaps:
    def test_valid_gaps_passes(self):
        payload = {
            "evidence": ["evidence1"],
            "gaps": [{"claim": "gap1", "basis": "basis1", "confidence": "high"}],
            "future_directions": ["direction1"],
        }
        result = _validate_stage("gaps", payload)
        assert result is payload

    def test_gaps_not_list_raises_error(self):
        payload = {
            "evidence": [],
            "gaps": "not a list",
            "future_directions": [],
        }
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("gaps", payload)
        assert "数组" in exc_info.value.message

    def test_missing_evidence_raises_error(self):
        payload = {"gaps": [], "future_directions": []}
        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _validate_stage("gaps", payload)
        assert "evidence" in exc_info.value.message


# ---------------------------------------------------------------------------
# STAGE_INSTRUCTIONS
# ---------------------------------------------------------------------------


class TestStageInstructions:
    def test_all_four_stages_present(self):
        assert set(STAGE_INSTRUCTIONS.keys()) == {"structure", "innovations", "methodology", "gaps"}

    def test_structure_instruction_mentions_json_fields(self):
        instruction = STAGE_INSTRUCTIONS["structure"]
        for field in ["title", "authors", "problem", "method", "datasets", "results", "limitations"]:
            assert field in instruction

    def test_innovations_instruction_mentions_items_and_confidence(self):
        instruction = STAGE_INSTRUCTIONS["innovations"]
        assert "items" in instruction
        assert "confidence" in instruction

    def test_methodology_instruction_mentions_reproducibility(self):
        instruction = STAGE_INSTRUCTIONS["methodology"]
        assert "reproducibility" in instruction
        assert "research_design" in instruction

    def test_gaps_instruction_mentions_evidence_and_future_directions(self):
        instruction = STAGE_INSTRUCTIONS["gaps"]
        assert "evidence" in instruction
        assert "future_directions" in instruction


class TestAnalysisContextBudget:
    def test_declared_context_length_rejects_oversized_input(self):
        model = type("Model", (), {"info": {"context_length": 1_024}, "model": object()})()

        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _ensure_analysis_context_budget(
                model,
                stage="structure",
                system_prompt="system",
                context="word " * 1_000,
            )

        assert exc_info.value.error_type == "analysis_context_exceeded"
        assert "structure" in exc_info.value.message
        assert "不会静默截断" in exc_info.value.message

    def test_runtime_input_limit_is_used_when_available(self):
        profile = {"max_input_tokens": 100}
        model = type("Model", (), {"info": {}, "model": type("Runtime", (), {"profile": profile})()})()

        with pytest.raises(AcademicPaperAnalysisError) as exc_info:
            _ensure_analysis_context_budget(
                model,
                stage="innovations",
                system_prompt="system",
                context="word " * 100,
            )

        assert "innovations" in exc_info.value.message
