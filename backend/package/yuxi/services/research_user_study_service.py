"""ResearchCompass 用户评测服务。

本模块是本仓库作者为科研平台设计的匿名用户评测业务：管理员创建评测并生成一次性
邀请令牌，参与者通过令牌匿名提交任务评分、SUS 量表与开放反馈，系统按匿名汇总
形成研究报告与 CSV 导出。Redis 限流与持久化由 Yuxi 提供；本模块定义评测邀请
令牌、SUS 评分计算与匿名化的问卷语义。
"""

from __future__ import annotations

import csv
import hashlib
import io
import secrets
import uuid
from collections import Counter
from statistics import mean
from typing import Any

from yuxi.repositories.research_user_study_repository import ResearchUserStudyRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.services.run_queue_service import get_redis_client
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_now_naive


TASK_SCORE_KEYS = ("search", "paper_analysis", "citation_traceability", "trend_insight")
SUS_SCORE_KEYS = tuple(f"q{index}" for index in range(1, 11))
DEFAULT_CONSENT_TEXT = (
    "您将试用科研罗盘的文献检索、论文分析、引用溯源与趋势分析能力，并完成一份匿名问卷。"
    "系统不会收集您的姓名、学号、联系方式、登录信息或设备标识。参与完全自愿，您可随时退出；"
    "提交后仅以匿名汇总形式用于项目用户评测。"
)
PUBLIC_STUDY_RESOLVE_LIMIT = 30
PUBLIC_STUDY_SUBMIT_LIMIT = 10
PUBLIC_STUDY_RATE_WINDOW_SECONDS = 60
_RATE_LIMIT_INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class ResearchUserStudyError(RuntimeError):
    def __init__(self, error_type: str, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.retry_after = retry_after


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _enforce_public_rate_limit(*, scope: str, client_key: str, limit: int) -> None:
    key = f"research:user-study:rate:{scope}:{_token_hash(client_key or 'unknown')}"
    try:
        redis = await get_redis_client()
        count = int(await redis.eval(_RATE_LIMIT_INCREMENT_SCRIPT, 1, key, PUBLIC_STUDY_RATE_WINDOW_SECONDS))
    except Exception as exc:
        raise ResearchUserStudyError(
            "rate_limit_unavailable",
            "匿名评测服务暂时不可用，请稍后重试",
            retry_after=PUBLIC_STUDY_RATE_WINDOW_SECONDS,
        ) from exc
    if count > limit:
        raise ResearchUserStudyError(
            "rate_limited",
            "请求过于频繁，请稍后重试",
            retry_after=PUBLIC_STUDY_RATE_WINDOW_SECONDS,
        )


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


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


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
            invite_status_counts = await self.repo.count_invites_by_status(study.study_id)
            result.append(
                {
                    **_public_study_payload(study),
                    "participant_count": sum(invite_status_counts.values()),
                    "submitted_count": invite_status_counts.get("submitted", 0),
                    "created_at": study.created_at.isoformat() if study.created_at else None,
                    "closed_at": study.closed_at.isoformat() if study.closed_at else None,
                }
            )
        return result

    async def get_study_report(
        self,
        *,
        kb_id: str,
        study_id: str,
        current_user: User,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        await _ensure_access(current_user, kb_id, write=True)
        study = await self.repo.get_study(study_id)
        if study is None or study.kb_id != kb_id:
            raise ResearchUserStudyError("study_not_found", "用户评测不存在")
        normalized_page = max(int(page), 1)
        normalized_page_size = min(max(int(page_size), 1), 500)
        invite_status_counts = await self.repo.count_invites_by_status(study_id)
        response_total = await self.repo.count_responses(study_id)
        summary_responses = []
        for offset in range(0, response_total, 500):
            batch, _ = await self.repo.list_responses(study_id, offset=offset, limit=500)
            summary_responses.extend(batch)
        responses, _ = await self.repo.list_responses(
            study_id,
            offset=(normalized_page - 1) * normalized_page_size,
            limit=normalized_page_size,
        )
        return {
            **_public_study_payload(study),
            "participant_count": sum(invite_status_counts.values()),
            "invite_status_counts": invite_status_counts,
            "summary": _summary(summary_responses),
            "responses": [_serialize_response(response) for response in responses],
            "responses_total": response_total,
            "page": normalized_page,
            "page_size": normalized_page_size,
            "has_more": normalized_page * normalized_page_size < response_total,
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
        await self.repo.update_study(study_id, {"status": "closed", "closed_at": utc_now_naive()})

    async def get_public_study(self, token: str, *, client_key: str = "unknown") -> dict[str, Any]:
        await _enforce_public_rate_limit(
            scope="resolve",
            client_key=client_key,
            limit=PUBLIC_STUDY_RESOLVE_LIMIT,
        )
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
        client_key: str = "unknown",
    ) -> dict[str, Any]:
        await _enforce_public_rate_limit(
            scope="submit",
            client_key=client_key,
            limit=PUBLIC_STUDY_SUBMIT_LIMIT,
        )
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
        response, rejection = await self.repo.create_response_and_use_invite(
            study_id=study.study_id,
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
                "submitted_at": utc_now_naive(),
            },
        )
        if rejection:
            error_messages = {
                "study_not_found": "用户评测不存在",
                "study_closed": "该用户评测已关闭",
                "invite_used": "该评测链接已提交",
            }
            message = error_messages.get(rejection)
            if message is None:
                raise RuntimeError(f"未知的用户评测提交拒绝原因: {rejection}")
            raise ResearchUserStudyError(rejection, message)
        if response is None:
            raise RuntimeError("用户评测响应事务未返回结果")
        return {"response_id": response.response_id, "submitted_at": response.submitted_at.isoformat()}

    async def export_study_csv(self, *, kb_id: str, study_id: str, current_user: User) -> tuple[str, str]:
        await _ensure_access(current_user, kb_id, write=True)
        study = await self.repo.get_study(study_id)
        if study is None or study.kb_id != kb_id:
            raise ResearchUserStudyError("study_not_found", "用户评测不存在")
        response_total = await self.repo.count_responses(study_id)
        responses = []
        for offset in range(0, response_total, 500):
            batch, _ = await self.repo.list_responses(study_id, offset=offset, limit=500)
            responses.extend(_serialize_response(response) for response in batch)
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
        for response in responses:
            writer.writerow(
                [
                    _csv_cell(value)
                    for value in [
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
                ]
            )
        return f"{study.study_id}-anonymous-responses.csv", output.getvalue()


__all__ = [
    "DEFAULT_CONSENT_TEXT",
    "ResearchUserStudyError",
    "ResearchUserStudyService",
    "SUS_SCORE_KEYS",
    "TASK_SCORE_KEYS",
    "_enforce_public_rate_limit",
]
