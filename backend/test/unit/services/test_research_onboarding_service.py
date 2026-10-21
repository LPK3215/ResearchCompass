"""research_onboarding_service 的四步完成判定测试。

引导状态只由真实数据决定：可访问知识库数量、用户上传文件数、成功检索与成功综述数。
这里用假仓储与假权限检查验证判定与下一步建议，不依赖数据库。
"""

from types import SimpleNamespace

import pytest

from yuxi.services import research_onboarding_service as service


def _patch(monkeypatch, *, accessible: int, progress: dict[str, int]):
    class FakeKnowledgeBaseRepository:
        async def get_all(self):
            # 第 0 条固定不可访问，其余 accessible 条可访问
            return [SimpleNamespace(kb_id=f"kb-{index}") for index in range(accessible + 1)]

    class FakeOnboardingRepository:
        async def count_user_progress(self, **kwargs):
            return progress

    async def check_accessible(user_info, kb_id):
        return kb_id != "kb-0"

    monkeypatch.setattr(service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(service, "ResearchOnboardingRepository", FakeOnboardingRepository)
    monkeypatch.setattr(service.knowledge_base, "check_accessible", check_accessible)


@pytest.mark.asyncio
async def test_new_user_has_all_steps_pending(monkeypatch):
    _patch(monkeypatch, accessible=0, progress={"uploaded": 0, "searches": 0, "syntheses": 0})

    result = await service.get_onboarding_state(
        current_user=SimpleNamespace(uid="user-1", role="user", department_id=None)
    )

    assert result["completed_count"] == 0
    assert result["all_completed"] is False
    assert result["next_step"] == "create_knowledge_base"
    assert [step["completed"] for step in result["steps"]] == [False, False, False, False]


@pytest.mark.asyncio
async def test_progress_comes_from_real_counts(monkeypatch):
    _patch(monkeypatch, accessible=2, progress={"uploaded": 3, "searches": 1, "syntheses": 0})

    result = await service.get_onboarding_state(
        current_user=SimpleNamespace(uid="user-1", role="user", department_id=None)
    )

    assert [step["completed"] for step in result["steps"]] == [True, True, True, False]
    assert result["completed_count"] == 3
    assert result["next_step"] == "run_synthesis"
    assert result["knowledge_base_count"] == 2
    assert result["uploaded_paper_count"] == 3
    assert result["search_run_count"] == 1
    assert result["synthesis_run_count"] == 0


@pytest.mark.asyncio
async def test_all_completed_returns_no_next_step(monkeypatch):
    _patch(monkeypatch, accessible=1, progress={"uploaded": 1, "searches": 2, "syntheses": 1})

    result = await service.get_onboarding_state(
        current_user=SimpleNamespace(uid="user-1", role="user", department_id=None)
    )

    assert result["all_completed"] is True
    assert result["next_step"] is None
    assert result["completed_count"] == len(result["steps"]) == 4
