import csv
import io
from types import SimpleNamespace

import pytest

from yuxi.services import research_user_study_service
from yuxi.services.research_user_study_service import (
    ResearchUserStudyError,
    ResearchUserStudyService,
    _enforce_public_rate_limit,
)


class FakeRedis:
    def __init__(self):
        self.counts = {}
        self.expirations = {}

    async def eval(self, script, numkeys, key, seconds):
        assert script == research_user_study_service._RATE_LIMIT_INCREMENT_SCRIPT
        assert numkeys == 1
        self.counts[key] = self.counts.get(key, 0) + 1
        if key not in self.expirations:
            self.expirations[key] = seconds
        return self.counts[key]

@pytest.mark.asyncio
async def test_public_rate_limit_rejects_requests_over_limit(monkeypatch):
    redis = FakeRedis()

    async def get_redis():
        return redis

    monkeypatch.setattr(research_user_study_service, "get_redis_client", get_redis)

    await _enforce_public_rate_limit(scope="submit", client_key="127.0.0.1", limit=2)
    await _enforce_public_rate_limit(scope="submit", client_key="127.0.0.1", limit=2)
    with pytest.raises(ResearchUserStudyError) as exc_info:
        await _enforce_public_rate_limit(scope="submit", client_key="127.0.0.1", limit=2)

    assert exc_info.value.error_type == "rate_limited"
    assert exc_info.value.retry_after == 60
    assert next(iter(redis.expirations.values())) == 60


@pytest.mark.asyncio
async def test_public_rate_limit_fails_closed_when_redis_is_unavailable(monkeypatch):
    async def get_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(research_user_study_service, "get_redis_client", get_redis)

    with pytest.raises(ResearchUserStudyError) as exc_info:
        await _enforce_public_rate_limit(scope="resolve", client_key="127.0.0.1", limit=1)

    assert exc_info.value.error_type == "rate_limit_unavailable"


@pytest.mark.asyncio
async def test_submit_reports_study_closed_when_atomic_repository_check_loses_close_race(monkeypatch):
    class FakeRepository:
        async def get_invite_by_hash(self, token_hash):
            return SimpleNamespace(invite_id="invite-1", study_id="study-1", status="pending")

        async def get_study(self, study_id):
            return SimpleNamespace(study_id=study_id, status="open")

        async def create_response_and_use_invite(self, **kwargs):
            assert kwargs["study_id"] == "study-1"
            return None, "study_closed"

    async def allow_rate_limit(**kwargs):
        return None

    monkeypatch.setattr(research_user_study_service, "_enforce_public_rate_limit", allow_rate_limit)
    service = ResearchUserStudyService()
    service.repo = FakeRepository()

    with pytest.raises(ResearchUserStudyError) as exc_info:
        await service.submit_public_response(
            token="valid-secret-token-for-test",
            consent=True,
            research_stage="master",
            research_experience="1_to_3_years",
            task_scores={key: 4 for key in research_user_study_service.TASK_SCORE_KEYS},
            sus_scores={key: 3 for key in research_user_study_service.SUS_SCORE_KEYS},
            overall_rating=4,
            recommend_score=8,
            feedback="",
        )

    assert exc_info.value.error_type == "study_closed"


@pytest.mark.asyncio
async def test_study_report_paginates_responses_but_summarizes_all(monkeypatch):
    responses = [
        SimpleNamespace(
            response_id=f"response-{index}",
            research_stage="master",
            research_experience="1_to_3_years",
            task_scores={key: 4 for key in research_user_study_service.TASK_SCORE_KEYS},
            sus_scores={key: 3 for key in research_user_study_service.SUS_SCORE_KEYS},
            overall_rating=4,
            recommend_score=8,
            feedback="",
            submitted_at=None,
        )
        for index in range(3)
    ]

    class FakeRepository:
        async def get_study(self, study_id):
            return SimpleNamespace(
                study_id=study_id,
                kb_id="kb-1",
                name="study",
                description="",
                consent_text="consent",
                status="open",
                created_at=None,
                closed_at=None,
            )

        async def count_invites_by_status(self, study_id):
            return {"submitted": 3}

        async def count_responses(self, study_id):
            return len(responses)

        async def list_responses(self, study_id, *, offset, limit):
            return responses[offset : offset + limit], len(responses)

    async def allow_access(current_user, kb_id, write=False):
        return None

    monkeypatch.setattr(research_user_study_service, "_ensure_access", allow_access)
    service = ResearchUserStudyService()
    service.repo = FakeRepository()

    report = await service.get_study_report(
        kb_id="kb-1",
        study_id="study-1",
        current_user=SimpleNamespace(uid="user-1"),
        page=2,
        page_size=2,
    )

    assert report["summary"]["response_count"] == 3
    assert [item["response_id"] for item in report["responses"]] == ["response-2"]
    assert report["responses_total"] == 3
    assert report["has_more"] is False


@pytest.mark.asyncio
async def test_study_csv_export_neutralizes_spreadsheet_formulas(monkeypatch):
    response = SimpleNamespace(
        response_id="response-1",
        research_stage="master",
        research_experience="1_to_3_years",
        task_scores={key: 4 for key in research_user_study_service.TASK_SCORE_KEYS},
        sus_scores={key: 3 for key in research_user_study_service.SUS_SCORE_KEYS},
        overall_rating=4,
        recommend_score=8,
        feedback="=HYPERLINK(\"https://example.test\",\"open\")",
        submitted_at=None,
    )

    class FakeRepository:
        async def get_study(self, study_id):
            return SimpleNamespace(study_id=study_id, kb_id="kb-1")

        async def count_responses(self, study_id):
            return 1

        async def list_responses(self, study_id, *, offset, limit):
            return [response], 1

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(research_user_study_service, "_ensure_access", allow_access)
    service = ResearchUserStudyService()
    service.repo = FakeRepository()

    _, content = await service.export_study_csv(
        kb_id="kb-1",
        study_id="study-1",
        current_user=SimpleNamespace(uid="admin"),
    )

    rows = list(csv.reader(io.StringIO(content)))
    assert rows[1][-2] == "'=HYPERLINK(\"https://example.test\",\"open\")"
