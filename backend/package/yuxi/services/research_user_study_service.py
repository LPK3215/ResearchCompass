from __future__ import annotations

import csv
import hashlib
import io
import secrets
import uuid
from collections import Counter
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from yuxi.repositories.research_user_study_repository import ResearchUserStudyRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User


TASK_SCORE_KEYS = ("search", "paper_analysis", "citation_traceability", "trend_insight")
SUS_SCORE_KEYS = tuple(f"q{index}" for index in range(1, 11))
DEFAULT_CONSENT_TEXT = (
    "您将试用科研罗盘的文献检索、论文分析、引用溯源与趋势分析能力，并完成一份匿名问卷。"
    "系统不会收集您的姓名、学号、联系方式、登录信息或设备标识。参与完全自愿，您可随时退出；"
    "提交后仅以匿名汇总形式用于项目用户评测。"
)


class ResearchUserStudyError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_study_payload(study) -> dict[str, Any]:
    return {
        "study_id": study.study_id,
        "name": study.name,
        "description": study.description or "",
        "consent_text": study.consent_text,
        "status": study.status,
    }


def _serialize_response(response) -> dict[str, Any]:
    return {
        "response_id": response.response_id,
        "research_stage": response.research_stage,
        "research_experience": response.research_experience,
        "task_scores": response.task_scores or {},
        "sus_scores": response.sus_scores or {},
        "overall_rating": response.overall_rating,
        "recommend_score": response.recommend_score,
        "feedback": response.feedback or "",
        "submitted_at": response.submitted_at.isoformat() if response.submitted_at else None,
    }


def _mean(values: list[int | float]) -> float | None:
    return round(float(mean(values)), 2) if values else None


def _sus_score(sus_scores: dict[str, int]) -> float:
    adjusted = []
    for index, key in enumerate(SUS_SCORE_KEYS, start=1):
        value = sus_scores[key]
        adjusted.append(value - 1 if index % 2 else 5 - value)
    return round(sum(adjusted) * 2.5, 1)


def _summary(responses: list[Any]) -> dict[str, Any]:
    task_scores = {key: _mean([response.task_scores[key] for response in responses]) for key in TASK_SCORE_KEYS}
    sus_scores = [_sus_score(response.sus_scores) for response in responses]
    return {
        "response_count": len(responses),
        "task_score_means": task_scores,
        "overall_rating_mean": _mean([response.overall_rating for response in responses]),
        "recommend_score_mean": _mean([response.recommend_score for response in responses]),
        "sus_mean": _mean(sus_scores),
        "research_stage_distribution": dict(Counter(response.research_stage for response in responses)),
        "research_experience_distribution": dict(Counter(response.research_experience for response in responses)),
    }


def _validate_score_map(
    values: dict[str, Any], keys: tuple[str, ...], *, minimum: int, maximum: int, label: str
) -> dict[str, int]:
    if set(values) != set(keys):
        raise ResearchUserStudyError("invalid_response", f"{label} 必须包含全部题目")
    normalized: dict[str, int] = {}
    for key in keys:
        value = values[key]
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ResearchUserStudyError("invalid_response", f"{label} 的 {key} 必须为 {minimum}–{maximum} 的整数")
        normalized[key] = value
    return normalized


class ResearchUserStudyService:
    def __init__(self) -> None:
        self.repo = ResearchUserStudyRepository()

    async def create_study(
        self,
        *,
        kb_id: str,
        current_user: User,
        name: str,
        description: str,
        participant_count: int,
        consent_text: str | None,
    ) -> dict[str, Any]:
        await _ensure_access(current_user, kb_id, write=True)
        if not 3 <= participant_count <= 20:
            raise ResearchUserStudyError("invalid_participant_count", "参与者数量必须为 3 到 20 人")
        study_id = f"study_{uuid.uuid4().hex[:12]}"
        invites = []
        public_invites = []
        for _ in range(participant_count):
            invite_id = f"invite_{uuid.uuid4().hex[:12]}"
            token = secrets.token_urlsafe(24)
            invites.append({"invite_id": invite_id, "study_id": study_id, "token_hash": _token_hash(token)})
            public_invites.append({"invite_id": invite_id, "token": token})
        study = await self.repo.create_study_with_invites(
            {
                "study_id": study_id,
                "kb_id": kb_id,
                "name": name.strip(),
                "description": description.strip(),
                "consent_text": (consent_text or DEFAULT_CONSENT_TEXT).strip(),
                "status": "open",
                "created_by": str(current_user.uid),
            },
            invites,
        )
        return {
            **_public_study_payload(study),
            "participant_count": participant_count,
            "submitted_count": 0,
            "invites": public_invites,
        }

    async def list_studies(self, *, kb_id: str, current_user: User) -> list[dict[str, Any]]:
        await _ensure_access(current_user, kb_id, write=True)
        studies = await self.repo.list_studies(kb_id)
        result = []
        for study in studies:
            invites = await self.repo.list_invites(study.study_id)
            submitted_count = await self.repo.count_responses(study.study_id)
            result.append(
                {
                    **_public_study_payload(study),
                    "participant_count": len(invites),
                    "submitted_count": submitted_count,
                    "created_at": study.created_at.isoformat() if study.created_at else None,
                    "closed_at": study.closed_at.isoformat() if study.closed_at else None,
                }
            )
        return result

    async def get_study_report(self, *, kb_id: str, study_id: str, current_user: User) -> dict[str, Any]:
        await _ensure_access(current_user, kb_id, write=True)
        study = await self.repo.get_study(study_id)
        if study is None or study.kb_id != kb_id:
            raise ResearchUserStudyError("study_not_found", "用户评测不存在")
        invites = await self.repo.list_invites(study_id)
        responses = await self.repo.list_responses(study_id)
        return {
            **_public_study_payload(study),
            "participant_count": len(invites),
            "invite_status_counts": dict(Counter(invite.status for invite in invites)),
            "summary": _summary(responses),
            "responses": [_serialize_response(response) for response in responses],
            "created_at": study.created_at.isoformat() if study.created_at else None,
            "closed_at": study.closed_at.isoformat() if study.closed_at else None,
        }

    async def close_study(self, *, kb_id: str, study_id: str, current_user: User) -> None:
        await _ensure_access(current_user, kb_id, write=True)
        study = await self.repo.get_study(study_id)
        if study is None or study.kb_id != kb_id:
            raise ResearchUserStudyError("study_not_found", "用户评测不存在")
        if study.status == "closed":
            return
        await self.repo.update_study(study_id, {"status": "closed", "closed_at": _now()})

    async def get_public_study(self, token: str) -> dict[str, Any]:
        invite = await self.repo.get_invite_by_hash(_token_hash(token))
        if invite is None:
            raise ResearchUserStudyError("invite_not_found", "评测链接无效")
        study = await self.repo.get_study(invite.study_id)
        if study is None:
            raise ResearchUserStudyError("study_not_found", "用户评测不存在")
        if invite.status == "submitted":
            raise ResearchUserStudyError("invite_used", "该评测链接已提交")
        if study.status != "open":
            raise ResearchUserStudyError("study_closed", "该用户评测已关闭")
        return _public_study_payload(study)

    async def submit_public_response(
        self,
        *,
        token: str,
        consent: bool,
        research_stage: str,
        research_experience: str,
        task_scores: dict[str, Any],
        sus_scores: dict[str, Any],
        overall_rating: int,
        recommend_score: int,
        feedback: str,
    ) -> dict[str, Any]:
        if consent is not True:
            raise ResearchUserStudyError("consent_required", "需要同意匿名参与说明后才能提交")
        invite = await self.repo.get_invite_by_hash(_token_hash(token))
        if invite is None:
            raise ResearchUserStudyError("invite_not_found", "评测链接无效")
        study = await self.repo.get_study(invite.study_id)
        if study is None:
            raise ResearchUserStudyError("study_not_found", "用户评测不存在")
        if study.status != "open":
            raise ResearchUserStudyError("study_closed", "该用户评测已关闭")
        if invite.status != "pending":
            raise ResearchUserStudyError("invite_used", "该评测链接已提交")
        if research_stage not in {"undergraduate", "master", "doctoral", "other"}:
            raise ResearchUserStudyError("invalid_response", "研究阶段无效")
        if research_experience not in {"none", "under_1_year", "1_to_3_years", "over_3_years"}:
            raise ResearchUserStudyError("invalid_response", "科研经验无效")
        normalized_tasks = _validate_score_map(task_scores, TASK_SCORE_KEYS, minimum=1, maximum=5, label="任务评分")
        normalized_sus = _validate_score_map(sus_scores, SUS_SCORE_KEYS, minimum=1, maximum=5, label="SUS 评分")
        if not isinstance(overall_rating, int) or isinstance(overall_rating, bool) or not 1 <= overall_rating <= 5:
            raise ResearchUserStudyError("invalid_response", "总体评分必须为 1–5 的整数")
        if not isinstance(recommend_score, int) or isinstance(recommend_score, bool) or not 0 <= recommend_score <= 10:
            raise ResearchUserStudyError("invalid_response", "推荐意愿必须为 0–10 的整数")
        if len(feedback) > 4000:
            raise ResearchUserStudyError("invalid_response", "文字反馈不能超过 4000 字")
        response = await self.repo.create_response_and_use_invite(
            invite_id=invite.invite_id,
            response_data={
                "response_id": f"response_{uuid.uuid4().hex[:12]}",
                "study_id": study.study_id,
                "invite_id": invite.invite_id,
                "research_stage": research_stage,
                "research_experience": research_experience,
                "task_scores": normalized_tasks,
                "sus_scores": normalized_sus,
                "overall_rating": overall_rating,
                "recommend_score": recommend_score,
                "feedback": feedback.strip() or None,
                "submitted_at": _now(),
            },
        )
        if response is None:
            raise ResearchUserStudyError("invite_used", "该评测链接已提交")
        return {"response_id": response.response_id, "submitted_at": response.submitted_at.isoformat()}

    async def export_study_csv(self, *, kb_id: str, study_id: str, current_user: User) -> tuple[str, str]:
        report = await self.get_study_report(kb_id=kb_id, study_id=study_id, current_user=current_user)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "response_id",
                "research_stage",
                "research_experience",
                *TASK_SCORE_KEYS,
                *SUS_SCORE_KEYS,
                "sus_score",
                "overall_rating",
                "recommend_score",
                "feedback",
                "submitted_at",
            ]
        )
        for response in report["responses"]:
            writer.writerow(
                [
                    response["response_id"],
                    response["research_stage"],
                    response["research_experience"],
                    *[response["task_scores"][key] for key in TASK_SCORE_KEYS],
                    *[response["sus_scores"][key] for key in SUS_SCORE_KEYS],
                    _sus_score(response["sus_scores"]),
                    response["overall_rating"],
                    response["recommend_score"],
                    response["feedback"],
                    response["submitted_at"],
                ]
            )
        return f"{report['study_id']}-anonymous-responses.csv", output.getvalue()


__all__ = [
    "DEFAULT_CONSENT_TEXT",
    "ResearchUserStudyError",
    "ResearchUserStudyService",
    "SUS_SCORE_KEYS",
    "TASK_SCORE_KEYS",
]
