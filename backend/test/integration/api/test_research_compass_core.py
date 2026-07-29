from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, TaskRecord, User
from yuxi.storage.postgres.models_knowledge import (
    AcademicPaper,
    AcademicPaperAnalysisRun,
    EvaluationDataset,
    EvaluationExperiment,
    KnowledgeChunk,
    KnowledgeFile,
    ResearchSearchRun,
    ResearchSynthesisRun,
    ResearchUserStudy,
    ResearchUserStudyInvite,
    ResearchUserStudyResponse,
)
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@asynccontextmanager
async def _temporary_superadmin(test_client):
    suffix = uuid.uuid4().hex[:12]
    uid = f"pytest_research_{suffix}"
    password = f"Pw!{suffix}"
    user_id = None
    department_id = None
    try:
        async with pg_manager.get_async_session_context() as session:
            department = Department(
                name=f"pytest_research_department_{suffix}",
                description="ResearchCompass integration test department",
            )
            session.add(department)
            await session.flush()
            department_id = department.id
            user = User(
                username=uid,
                uid=uid,
                password_hash=AuthUtils.hash_password(password),
                role="superadmin",
                department_id=department_id,
            )
            session.add(user)
            await session.flush()
            user_id = user.id

        login_response = await test_client.post("/api/auth/token", data={"username": uid, "password": password})
        assert login_response.status_code == 200, login_response.text
        token = login_response.json()["access_token"]
        yield {"headers": {"Authorization": f"Bearer {token}"}, "uid": uid}
    finally:
        if user_id is not None:
            async with pg_manager.get_async_session_context() as session:
                user = await session.get(User, user_id)
                if user is not None:
                    await session.delete(user)
                department = await session.get(Department, department_id)
                if department is not None:
                    await session.delete(department)


async def _create_knowledge_database(test_client, headers) -> dict:
    response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": f"pytest_research_{uuid.uuid4().hex[:12]}",
            "description": "ResearchCompass core API integration test",
            "embedding_model_spec": "siliconflow-cn:BAAI/bge-m3",
            "kb_type": "milvus",
            "additional_params": {},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_research_copilot_thread_reuses_scope_and_isolates_projects(test_client):
    knowledge_databases: list[str] = []
    project_ids: list[str] = []

    async with _temporary_superadmin(test_client) as admin:
        headers = admin["headers"]
        try:
            primary_kb = await _create_knowledge_database(test_client, headers)
            knowledge_databases.append(primary_kb["kb_id"])
            other_kb = await _create_knowledge_database(test_client, headers)
            knowledge_databases.append(other_kb["kb_id"])

            for title in ("Copilot project A", "Copilot project B"):
                project_response = await test_client.post(
                    f"/api/research/databases/{primary_kb['kb_id']}/projects",
                    json={
                        "title": title,
                        "research_question": f"How does {title} preserve research scope?",
                    },
                    headers=headers,
                )
                assert project_response.status_code == 201, project_response.text
                project_ids.append(project_response.json()["project_id"])

            unauthenticated = await test_client.post(
                "/api/research/copilot/thread",
                json={"kb_id": primary_kb["kb_id"], "project_id": project_ids[0]},
            )
            assert unauthenticated.status_code == 401

            concurrent_responses = await asyncio.gather(
                *(
                    test_client.post(
                        "/api/research/copilot/thread",
                        json={
                            "kb_id": primary_kb["kb_id"],
                            "project_id": project_ids[0],
                            "surface": "projects",
                        },
                        headers=headers,
                    )
                    for _ in range(4)
                )
            )
            for response in concurrent_responses:
                assert response.status_code == 200, response.text
            concurrent_threads = [response.json() for response in concurrent_responses]
            assert len({item["thread"]["id"] for item in concurrent_threads}) == 1

            first = concurrent_threads[0]
            assert first["agent_id"] == "research-copilot"
            assert first["research_context"]["project_id"] == project_ids[0]
            assert uuid.UUID(first["thread"]["id"]).version == 4

            reused_response = await test_client.post(
                "/api/research/copilot/thread",
                json={
                    "kb_id": primary_kb["kb_id"],
                    "project_id": project_ids[0],
                    "surface": "synthesis",
                },
                headers=headers,
            )
            assert reused_response.status_code == 200, reused_response.text
            reused = reused_response.json()
            assert reused["thread"]["id"] == first["thread"]["id"]
            assert reused["thread"]["metadata"]["research_context"]["surface"] == "synthesis"

            isolated_response = await test_client.post(
                "/api/research/copilot/thread",
                json={"kb_id": primary_kb["kb_id"], "project_id": project_ids[1]},
                headers=headers,
            )
            assert isolated_response.status_code == 200, isolated_response.text
            assert isolated_response.json()["thread"]["id"] != first["thread"]["id"]

            mismatched_response = await test_client.post(
                "/api/research/copilot/thread",
                json={"kb_id": other_kb["kb_id"], "project_id": project_ids[0]},
                headers=headers,
            )
            assert mismatched_response.status_code == 409, mismatched_response.text
            assert mismatched_response.json()["detail"]["error"] == "project_scope_mismatch"
        finally:
            for project_id in project_ids:
                response = await test_client.delete(f"/api/research/projects/{project_id}", headers=headers)
                assert response.status_code in {204, 404}, response.text
            for kb_id in reversed(knowledge_databases):
                response = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=headers)
                assert response.status_code in {200, 404}, response.text


async def _seed_academic_paper(kb_id: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:12]
    file_id = f"file_{suffix}"
    paper_id = f"paper_{suffix}"
    chunk_ids = [f"chunk_{suffix}_{index}" for index in range(2)]

    async with pg_manager.get_async_session_context() as session:
        session.add(
            KnowledgeFile(
                file_id=file_id,
                kb_id=kb_id,
                filename="research-compass-integration.md",
                original_filename="research-compass-integration.md",
                file_type="md",
                status="done",
                chunk_count=2,
                token_count=40,
                created_by="pytest",
            )
        )
        await session.flush()
        session.add(
            AcademicPaper(
                paper_id=paper_id,
                kb_id=kb_id,
                file_id=file_id,
                title="Evidence-grounded Research Opportunity Discovery",
                abstract="A reproducible study of evidence-grounded research opportunity discovery.",
                authors=["Test Researcher"],
                publication_year=2025,
                venue="ResearchCompass Integration",
                doi=f"10.0000/{suffix}",
                keywords=["Research Compass", "research compass", "Evidence"],
                language="en",
                external_ids={"DOI": f"10.0000/{suffix}"},
                citation_count=12,
                metadata_source="document",
                metadata_status="verified",
                metadata_revision=1,
                indexed_revision=1,
            )
        )
        session.add_all(
            [
                KnowledgeChunk(
                    chunk_id=chunk_ids[0],
                    file_id=file_id,
                    kb_id=kb_id,
                    chunk_index=0,
                    content="The introduction defines an evidence-grounded research workflow.",
                    start_char_pos=0,
                    end_char_pos=68,
                    chunk_metadata={
                        "document_type": "academic_paper",
                        "section_type": "introduction",
                        "section_title": "Introduction",
                        "section_path": ["Introduction"],
                    },
                ),
                KnowledgeChunk(
                    chunk_id=chunk_ids[1],
                    file_id=file_id,
                    kb_id=kb_id,
                    chunk_index=1,
                    content="The experiments report transparent evidence and reproducible scoring.",
                    start_char_pos=69,
                    end_char_pos=137,
                    chunk_metadata={
                        "document_type": "academic_paper",
                        "section_type": "experiments",
                        "section_title": "Experiments",
                        "section_path": ["Experiments"],
                    },
                ),
            ]
        )

    return {"paper_id": paper_id, "file_id": file_id, "first_chunk_id": chunk_ids[0]}


async def test_research_compass_core_business_without_semantic_scholar(
    test_client,
):
    run_ids: list[str] = []
    task_ids: list[str] = []
    study_record_ids: dict[str, str] = {}
    async with _temporary_superadmin(test_client) as admin:
        admin_headers = admin["headers"]
        knowledge_database = await _create_knowledge_database(test_client, admin_headers)
        kb_id = knowledge_database["kb_id"]
        try:
            run_ids, task_ids, study_record_ids = await _exercise_research_compass_core_business(
                test_client,
                admin_headers,
                admin["uid"],
                kb_id,
            )
        finally:
            delete_response = await test_client.delete(
                f"/api/knowledge/databases/{kb_id}",
                headers=admin_headers,
            )
            assert delete_response.status_code in {200, 404}, delete_response.text
            if run_ids:
                async with pg_manager.get_async_session_context() as session:
                    remaining = await session.scalar(
                        select(func.count())
                        .select_from(ResearchSynthesisRun)
                        .where(ResearchSynthesisRun.run_id.in_(run_ids))
                    )
                assert int(remaining or 0) == 0
            if task_ids:
                async with pg_manager.get_async_session_context() as session:
                    remaining_tasks = await session.scalar(
                        select(func.count()).select_from(TaskRecord).where(TaskRecord.id.in_(task_ids))
                    )
                assert int(remaining_tasks or 0) == 0
            if study_record_ids:
                async with pg_manager.get_async_session_context() as session:
                    remaining_study_records = {
                        "study": await session.scalar(
                            select(func.count())
                            .select_from(ResearchUserStudy)
                            .where(ResearchUserStudy.study_id == study_record_ids["study_id"])
                        ),
                        "invite": await session.scalar(
                            select(func.count())
                            .select_from(ResearchUserStudyInvite)
                            .where(ResearchUserStudyInvite.invite_id == study_record_ids["invite_id"])
                        ),
                        "response": await session.scalar(
                            select(func.count())
                            .select_from(ResearchUserStudyResponse)
                            .where(ResearchUserStudyResponse.response_id == study_record_ids["response_id"])
                        ),
                    }
                assert remaining_study_records == {"study": 0, "invite": 0, "response": 0}


async def _exercise_research_compass_core_business(
    test_client,
    admin_headers: dict[str, str],
    admin_uid: str,
    kb_id: str,
) -> tuple[list[str], list[str], dict[str, str]]:
    seeded = await _seed_academic_paper(kb_id)

    papers_response = await test_client.get(
        f"/api/research/databases/{kb_id}/papers",
        params={"page": 1, "page_size": 10, "sort_by": "year", "sort_order": "desc"},
        headers=admin_headers,
    )
    assert papers_response.status_code == 200, papers_response.text
    papers = papers_response.json()
    assert papers["total"] == 1
    assert papers["items"][0]["paper_id"] == seeded["paper_id"]
    assert papers["items"][0]["publication_year"] == 2025
    assert papers["has_more"] is False

    for export_params in ({"paper_ids": seeded["paper_id"]}, None):
        bibtex_response = await test_client.get(
            f"/api/research/databases/{kb_id}/papers/export",
            params=export_params,
            headers=admin_headers,
        )
        assert bibtex_response.status_code == 200, bibtex_response.text
        assert bibtex_response.content.startswith(b"\xef\xbb\xbf")
        bibtex = bibtex_response.content.decode("utf-8-sig")
        assert "title = {Evidence-grounded Research Opportunity Discovery}" in bibtex
        assert bibtex.count("@article{") == 1

    detail_response = await test_client.get(
        f"/api/research/databases/{kb_id}/papers/{seeded['paper_id']}",
        headers=admin_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["file"]["chunk_count"] == 2
    assert detail["section_counts"] == {"introduction": 1, "experiments": 1}
    assert "content" not in detail["file"]

    chunks_response = await test_client.get(
        f"/api/research/databases/{kb_id}/papers/{seeded['paper_id']}/chunks",
        params={"section_type": "introduction", "offset": 0, "limit": 10},
        headers=admin_headers,
    )
    assert chunks_response.status_code == 200, chunks_response.text
    chunks = chunks_response.json()
    assert chunks["total"] == 1
    assert chunks["items"][0]["chunk_id"] == seeded["first_chunk_id"]
    assert chunks["items"][0]["metadata"]["section_type"] == "introduction"
    assert chunks["items"][0]["start_char_pos"] == 0

    await _exercise_research_search_history_api(
        test_client,
        admin_headers=admin_headers,
        kb_id=kb_id,
        uid=admin_uid,
        seeded=seeded,
    )

    trends_response = await test_client.get(
        f"/api/research/databases/{kb_id}/trends",
        params={"top_keywords": 10},
        headers=admin_headers,
    )
    assert trends_response.status_code == 200, trends_response.text
    trends = trends_response.json()
    assert trends["scope"]["total_papers"] == 1
    assert trends["publication_trend"] == [{"year": 2025, "papers": 1, "citations": 12}]
    keyword_totals = {item["keyword"].casefold(): item["total"] for item in trends["keyword_totals"]}
    assert keyword_totals["research compass"] == 1
    assert keyword_totals["evidence"] == 1

    opportunities_response = await test_client.get(
        f"/api/research/databases/{kb_id}/opportunities",
        params={"limit": 10},
        headers=admin_headers,
    )
    assert opportunities_response.status_code == 200, opportunities_response.text
    opportunities = opportunities_response.json()
    assert opportunities["scope"]["total_papers"] == 1
    opportunity_keywords = {item["keyword"].casefold() for item in opportunities["opportunities"]}
    assert {"research compass", "evidence"} <= opportunity_keywords
    first_opportunity = opportunities["opportunities"][0]
    assert first_opportunity["score"] > 0
    assert first_opportunity["evidence_papers"][0]["paper_id"] == seeded["paper_id"]
    assert first_opportunity["coverage"]["graph_coverage_ratio"] == 0
    assert "未计入图谱缺口分" in first_opportunity["reasons"][-1]

    create_study_response = await test_client.post(
        f"/api/research/databases/{kb_id}/user-studies",
        json={
            "name": "ResearchCompass integration study",
            "description": "Core business verification",
            "participant_count": 3,
        },
        headers=admin_headers,
    )
    assert create_study_response.status_code == 200, create_study_response.text
    study = create_study_response.json()
    assert len(study["invites"]) == 3
    token = study["invites"][0]["token"]

    resolve_response = await test_client.post(
        "/api/research/user-studies/public/resolve",
        json={"token": token},
    )
    assert resolve_response.status_code == 200, resolve_response.text
    assert resolve_response.json()["study_id"] == study["study_id"]

    submission = {
        "token": token,
        "consent": True,
        "research_stage": "master",
        "research_experience": "1_to_3_years",
        "task_scores": {
            "search": 5,
            "paper_analysis": 4,
            "citation_traceability": 4,
            "trend_insight": 5,
        },
        "sus_scores": {f"q{index}": 5 if index % 2 else 1 for index in range(1, 11)},
        "overall_rating": 5,
        "recommend_score": 9,
        "feedback": "The evidence trail is clear.",
    }
    submit_response = await test_client.post(
        "/api/research/user-studies/public/responses",
        json=submission,
    )
    assert submit_response.status_code == 200, submit_response.text
    response_id = submit_response.json()["response_id"]
    assert response_id.startswith("response_")

    duplicate_response = await test_client.post(
        "/api/research/user-studies/public/responses",
        json=submission,
    )
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"]["error"] == "invite_used"

    report_response = await test_client.get(
        f"/api/research/databases/{kb_id}/user-studies/{study['study_id']}",
        params={"page": 1, "page_size": 1},
        headers=admin_headers,
    )
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert report["summary"]["response_count"] == 1
    assert report["responses_total"] == 1
    assert report["responses"][0]["feedback"] == "The evidence trail is clear."
    assert report["has_more"] is False

    export_response = await test_client.get(
        f"/api/research/databases/{kb_id}/user-studies/{study['study_id']}/export",
        headers=admin_headers,
    )
    assert export_response.status_code == 200, export_response.text
    assert "The evidence trail is clear." in export_response.content.decode("utf-8-sig")

    success_run_id, pending_run_id, pending_task_id = await _seed_synthesis_runs(
        kb_id=kb_id,
        uid=admin_uid,
        seeded=seeded,
    )
    await _exercise_research_synthesis_api(
        test_client,
        admin_headers=admin_headers,
        kb_id=kb_id,
        success_run_id=success_run_id,
        pending_run_id=pending_run_id,
        pending_task_id=pending_task_id,
    )
    await _exercise_research_project_api(
        test_client,
        admin_headers=admin_headers,
        admin_uid=admin_uid,
        kb_id=kb_id,
        seeded=seeded,
        synthesis_run_id=success_run_id,
    )
    await _assert_active_synthesis_unique_constraint(kb_id=kb_id, uid=admin_uid)
    return (
        [success_run_id, pending_run_id],
        [pending_task_id],
        {
            "study_id": study["study_id"],
            "invite_id": study["invites"][0]["invite_id"],
            "response_id": response_id,
        },
    )


async def _exercise_research_search_history_api(
    test_client,
    *,
    admin_headers: dict[str, str],
    kb_id: str,
    uid: str,
    seeded: dict[str, str],
) -> None:
    success_run_id = f"search_{uuid.uuid4().hex[:12]}"
    running_run_id = f"search_{uuid.uuid4().hex[:12]}"
    result_snapshot = {
        "run_id": success_run_id,
        "query": "How reliable is the evidence workflow?",
        "rewritten_query": "reliable evidence workflow",
        "keywords": ["evidence", "workflow"],
        "items": [
            {
                "paper_id": seeded["paper_id"],
                "title": "Evidence-grounded Research Opportunity Discovery",
                "authors": ["Test Researcher"],
                "publication_year": 2025,
                "ranking_score": 0.91,
                "scores": {"rerank": 0.91},
                "evidence": [
                    {
                        "chunk_id": seeded["first_chunk_id"],
                        "file_id": seeded["file_id"],
                        "content": "The introduction defines an evidence-grounded research workflow.",
                        "section_title": "Introduction",
                        "locator": {"chunk_id": seeded["first_chunk_id"]},
                    }
                ],
            }
        ],
        "total": 1,
        "graph_expansion": None,
        "config": {"mode": "local_hybrid", "top_k": 10, "recall_top_k": 50},
        "stage_timings": {"retrieval_ms": 2},
    }
    async with pg_manager.get_async_session_context() as session:
        session.add_all(
            [
                ResearchSearchRun(
                    run_id=success_run_id,
                    kb_id=kb_id,
                    uid=uid,
                    raw_query=result_snapshot["query"],
                    rewritten_query=result_snapshot["rewritten_query"],
                    rewrite_keywords=result_snapshot["keywords"],
                    model_config_json={"chat_model": "test-chat", "reranker_model": "test-reranker"},
                    retrieval_config=result_snapshot["config"],
                    status="success",
                    stage_timings=result_snapshot["stage_timings"],
                    result_count=1,
                    result_snapshot=result_snapshot,
                ),
                ResearchSearchRun(
                    run_id=running_run_id,
                    kb_id=kb_id,
                    uid=uid,
                    raw_query="Running query",
                    model_config_json={},
                    retrieval_config={"mode": "local_hybrid"},
                    status="running",
                    stage_timings={},
                ),
                ResearchSearchRun(
                    run_id=f"search_{uuid.uuid4().hex[:12]}",
                    kb_id=kb_id,
                    uid="another-user",
                    raw_query="Other user's query",
                    model_config_json={},
                    retrieval_config={"mode": "local_hybrid"},
                    status="success",
                    stage_timings={},
                ),
            ]
        )

    list_response = await test_client.get(
        f"/api/research/databases/{kb_id}/search-runs",
        params={"offset": 0, "limit": 20},
        headers=admin_headers,
    )
    assert list_response.status_code == 200, list_response.text
    history = list_response.json()
    assert history["total"] == 2
    assert {item["run_id"] for item in history["items"]} == {success_run_id, running_run_id}
    assert all("result" not in item for item in history["items"])

    pin_response = await test_client.patch(
        f"/api/research/search-runs/{success_run_id}",
        json={"is_pinned": True},
        headers=admin_headers,
    )
    assert pin_response.status_code == 200, pin_response.text
    assert pin_response.json()["is_pinned"] is True

    detail_response = await test_client.get(
        f"/api/research/search-runs/{success_run_id}",
        headers=admin_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["result"]["items"][0]["paper_id"] == seeded["paper_id"]
    assert detail["result"]["items"][0]["evidence"][0]["chunk_id"] == seeded["first_chunk_id"]

    running_delete_response = await test_client.delete(
        f"/api/research/search-runs/{running_run_id}",
        headers=admin_headers,
    )
    assert running_delete_response.status_code == 409
    assert running_delete_response.json()["detail"] == {
        "error": "run_active",
        "message": "运行中的检索不能删除",
    }

    delete_response = await test_client.delete(
        f"/api/research/search-runs/{success_run_id}",
        headers=admin_headers,
    )
    assert delete_response.status_code == 204, delete_response.text
    missing_response = await test_client.get(
        f"/api/research/search-runs/{success_run_id}",
        headers=admin_headers,
    )
    assert missing_response.status_code == 404


def _synthesis_result(seeded: dict[str, str]) -> dict:
    return {
        "question": "How reliable is the evidence workflow?",
        "executive_summary": {"text": "The workflow has verified local evidence.", "claim_ids": ["C1"]},
        "themes": [{"title": "Evidence", "summary": "The available chunk supports the claim.", "claim_ids": ["C1"]}],
        "claims": [
            {
                "claim_id": "C1",
                "statement": "The workflow exposes a clear evidence trail.",
                "confidence": "medium",
                "paper_ids": [seeded["paper_id"]],
                "evidence": [
                    {
                        "chunk_id": seeded["first_chunk_id"],
                        "paper_id": seeded["paper_id"],
                        "paper_title": "Evidence-grounded Research Opportunity Discovery",
                        "section_type": "introduction",
                        "section_title": "Introduction",
                        "locator": {"page": 1},
                    }
                ],
            }
        ],
        "contradictions": [],
        "limitations": [],
        "research_gaps": [],
        "unsupported_claims": [],
        "coverage": {
            "supported_claims": 1,
            "total_claims": 1,
            "supported_conclusions": 1,
            "total_conclusions": 1,
            "citation_coverage_ratio": 1.0,
            "distinct_papers": 1,
            "retrieved_papers": 1,
            "retrieved_chunks": 2,
        },
        "sources": [
            {
                "paper_id": seeded["paper_id"],
                "title": "Evidence-grounded Research Opportunity Discovery",
                "authors": ["Test Researcher"],
                "publication_year": 2025,
                "venue": "ResearchCompass Integration",
                "doi": None,
                "evidence_chunk_ids": [seeded["first_chunk_id"]],
            }
        ],
        "retrieval": {"mode": "local_hybrid", "search_run_id": "search-integration", "keywords": ["evidence"]},
        "methodological_constraints": ["Integration seed result is bounded to local evidence."],
        "validation": {
            "status": "verified",
            "rules_version": "evidence-synthesis-v1",
            "validated_at": "2026-01-01T00:00:00",
            "confidence_adjustments": [],
        },
    }


async def _seed_synthesis_runs(*, kb_id: str, uid: str, seeded: dict[str, str]) -> tuple[str, str, str]:
    success_run_id = f"synthesis_{uuid.uuid4().hex[:12]}"
    pending_run_id = f"synthesis_{uuid.uuid4().hex[:12]}"
    pending_task_id = uuid.uuid4().hex
    async with pg_manager.get_async_session_context() as session:
        success_run = ResearchSynthesisRun(
            run_id=success_run_id,
            kb_id=kb_id,
            uid=uid,
            raw_query="How reliable is the evidence workflow?",
            model_config_json={"model": "test-chat", "reranker_model": "test-reranker"},
            retrieval_config={"mode": "local_hybrid", "top_k": 2, "recall_top_k": 20},
            status="success",
            stage="completed",
            retrieval_snapshot={"papers": []},
            result=_synthesis_result(seeded),
            stage_timings={"validation_ms": 1},
        )
        session.add(success_run)
        await session.flush()
        success_run.active_key = None
        await session.flush()
        session.add(
            TaskRecord(
                id=pending_task_id,
                name="pytest pending synthesis",
                type="research_synthesis",
                status="pending",
                progress=0,
                message="等待执行",
                payload={"run_id": pending_run_id, "kb_id": kb_id, "uid": uid},
                cancel_requested=0,
            )
        )
        session.add(
            ResearchSynthesisRun(
                run_id=pending_run_id,
                kb_id=kb_id,
                uid=uid,
                raw_query="Pending synthesis should not export",
                model_config_json={"model": "test-chat", "reranker_model": "test-reranker"},
                retrieval_config={"mode": "local_hybrid", "top_k": 2, "recall_top_k": 20},
                active_key="active",
                task_id=pending_task_id,
                status="pending",
                stage="pending",
                stage_timings={},
            )
        )
    return success_run_id, pending_run_id, pending_task_id


async def _exercise_research_synthesis_api(
    test_client,
    *,
    admin_headers: dict[str, str],
    kb_id: str,
    success_run_id: str,
    pending_run_id: str,
    pending_task_id: str,
) -> None:
    list_response = await test_client.get(
        f"/api/research/databases/{kb_id}/syntheses",
        headers=admin_headers,
    )
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()
    listed_ids = {item["run_id"] for item in listed["items"]}
    assert {success_run_id, pending_run_id} <= listed_ids
    assert listed["total"] >= 2

    detail_response = await test_client.get(
        f"/api/research/synthesis-runs/{success_run_id}",
        headers=admin_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["status"] == "success"
    assert detail["result"]["validation"]["status"] == "verified"
    assert (
        detail["result"]["claims"][0]["evidence"][0]["chunk_id"]
        == detail["result"]["sources"][0]["evidence_chunk_ids"][0]
    )

    markdown_response = await test_client.get(
        f"/api/research/synthesis-runs/{success_run_id}/export",
        params={"format": "markdown"},
        headers=admin_headers,
    )
    assert markdown_response.status_code == 200, markdown_response.text
    assert "证据约束研究综述" in markdown_response.content.decode("utf-8")
    assert f"chunk_id={detail['result']['sources'][0]['evidence_chunk_ids'][0]}" in markdown_response.content.decode(
        "utf-8"
    )

    docx_response = await test_client.get(
        f"/api/research/synthesis-runs/{success_run_id}/export",
        params={"format": "docx"},
        headers=admin_headers,
    )
    assert docx_response.status_code == 200, docx_response.text
    assert docx_response.content.startswith(b"PK")

    pending_export_response = await test_client.get(
        f"/api/research/synthesis-runs/{pending_run_id}/export",
        params={"format": "markdown"},
        headers=admin_headers,
    )
    assert pending_export_response.status_code == 409
    assert pending_export_response.json()["detail"]["error"] == "synthesis_not_exportable"

    cancel_response = await test_client.post(
        f"/api/research/synthesis-runs/{pending_run_id}/cancel",
        headers=admin_headers,
    )
    assert cancel_response.status_code == 200, cancel_response.text
    cancelled = cancel_response.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["task_id"] == pending_task_id

    duplicate_cancel = await test_client.post(
        f"/api/research/synthesis-runs/{pending_run_id}/cancel",
        headers=admin_headers,
    )
    assert duplicate_cancel.status_code == 409
    assert duplicate_cancel.json()["detail"]["error"] == "synthesis_not_cancellable"

    async with pg_manager.get_async_session_context() as session:
        task = await session.get(TaskRecord, pending_task_id)
        assert task is not None
        task.status = "cancelled"


async def _exercise_research_project_api(
    test_client,
    *,
    admin_headers: dict[str, str],
    admin_uid: str,
    kb_id: str,
    seeded: dict[str, str],
    synthesis_run_id: str,
) -> None:
    suffix = uuid.uuid4().hex[:12]
    search_run_id = f"project_search_{suffix}"
    analysis_run_id = f"project_analysis_{suffix}"
    dataset_id = f"project_dataset_{suffix}"
    experiment_id = f"project_experiment_{suffix}"
    async with pg_manager.get_async_session_context() as session:
        paper = await session.scalar(
            select(AcademicPaper).where(
                AcademicPaper.kb_id == kb_id,
                AcademicPaper.paper_id == seeded["paper_id"],
            )
        )
        assert paper is not None
        session.add_all(
            [
                ResearchSearchRun(
                    run_id=search_run_id,
                    kb_id=kb_id,
                    uid=admin_uid,
                    raw_query="Project-scoped evidence search",
                    rewritten_query="project evidence",
                    model_config_json={},
                    retrieval_config={"mode": "local_hybrid"},
                    status="success",
                    stage_timings={},
                    result_count=1,
                    result_snapshot={"items": []},
                ),
                AcademicPaperAnalysisRun(
                    run_id=analysis_run_id,
                    kb_id=kb_id,
                    academic_paper_id=paper.id,
                    uid=admin_uid,
                    model_config_json={},
                    strategy="multi_agent",
                    status="success",
                    stage="completed",
                    result={"summary": "Verified paper analysis"},
                ),
                EvaluationDataset(
                    dataset_id=dataset_id,
                    kb_id=kb_id,
                    name="Project evaluation dataset",
                    description="Dataset for research project integration coverage",
                    item_count=1,
                    has_gold_chunks=True,
                    has_gold_answers=False,
                    created_by=admin_uid,
                ),
            ]
        )
        await session.flush()
        session.add(
            EvaluationExperiment(
                experiment_id=experiment_id,
                name="Project ablation experiment",
                description="Reproducible experiment linked to the project",
                source_kb_id=kb_id,
                dataset_id=dataset_id,
                status="completed",
                dataset_fingerprint=f"sha256:{suffix}",
                corpus_snapshot={},
                shared_config={},
                comparison_report={"overall_score": 0.82},
                total_variants=2,
                completed_variants=2,
                created_by=admin_uid,
            )
        )

    create_response = await test_client.post(
        f"/api/research/databases/{kb_id}/projects",
        json={
            "title": "Evidence-grounded project workspace",
            "research_question": "How can a complete evidence trail support this research question?",
            "description": "Integration coverage for a consumer research project.",
            "tags": ["evidence", "reproducibility"],
            "target_date": "2027-06-30",
            "next_action": "Collect the first evidence set",
        },
        headers=admin_headers,
    )
    assert create_response.status_code == 201, create_response.text
    project = create_response.json()
    project_id = project["project_id"]
    assert project["status"] == "active"
    assert project["asset_counts"]["total"] == 0

    list_response = await test_client.get(
        f"/api/research/databases/{kb_id}/projects",
        params={"status": "active", "query": "evidence-grounded", "offset": 0, "limit": 20},
        headers=admin_headers,
    )
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()
    assert listed["total"] == 1
    assert listed["items"][0]["project_id"] == project_id
    assert listed["has_more"] is False

    references = {
        "paper": seeded["paper_id"],
        "search_run": search_run_id,
        "synthesis_run": synthesis_run_id,
        "analysis_run": analysis_run_id,
        "evaluation_experiment": experiment_id,
    }
    for asset_type, reference_id in references.items():
        candidates_response = await test_client.get(
            f"/api/research/projects/{project_id}/asset-candidates",
            params={"asset_type": asset_type, "offset": 0, "limit": 100},
            headers=admin_headers,
        )
        assert candidates_response.status_code == 200, candidates_response.text
        candidate = next(item for item in candidates_response.json()["items"] if item["reference_id"] == reference_id)
        assert candidate["linked"] is False

        add_response = await test_client.post(
            f"/api/research/projects/{project_id}/assets",
            json={"asset_type": asset_type, "reference_ids": [reference_id], "notes": f"{asset_type} note"},
            headers=admin_headers,
        )
        assert add_response.status_code == 201, add_response.text
        assert add_response.json()["items"][0]["available"] is True

    duplicate_response = await test_client.post(
        f"/api/research/projects/{project_id}/assets",
        json={"asset_type": "paper", "reference_ids": [seeded["paper_id"]]},
        headers=admin_headers,
    )
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"]["error"] == "asset_already_linked"

    assets_response = await test_client.get(
        f"/api/research/projects/{project_id}/assets",
        params={"offset": 0, "limit": 3},
        headers=admin_headers,
    )
    assert assets_response.status_code == 200, assets_response.text
    assets_page = assets_response.json()
    assert assets_page["total"] == 5
    assert len(assets_page["items"]) == 3
    assert assets_page["has_more"] is True

    paper_assets_response = await test_client.get(
        f"/api/research/projects/{project_id}/assets",
        params={"asset_type": "paper", "query": "Evidence-grounded", "offset": 0, "limit": 20},
        headers=admin_headers,
    )
    assert paper_assets_response.status_code == 200, paper_assets_response.text
    paper_asset = paper_assets_response.json()["items"][0]
    assert paper_asset["reference_id"] == seeded["paper_id"]

    milestone_response = await test_client.post(
        f"/api/research/projects/{project_id}/milestones",
        json={
            "title": "Validate the evidence base",
            "description": "Complete the provenance review before delivery.",
            "status": "active",
            "target_date": "2027-05-31",
        },
        headers=admin_headers,
    )
    assert milestone_response.status_code == 201, milestone_response.text
    milestone_id = milestone_response.json()["milestone_id"]

    task_response = await test_client.post(
        f"/api/research/projects/{project_id}/tasks",
        json={
            "title": "Verify the strongest source",
            "description": "Check the source against the synthesis claims.",
            "status": "in_progress",
            "priority": "high",
            "due_date": "2027-05-15",
            "milestone_id": milestone_id,
        },
        headers=admin_headers,
    )
    assert task_response.status_code == 201, task_response.text
    task_id = task_response.json()["task_id"]

    link_response = await test_client.post(
        f"/api/research/projects/{project_id}/plan/asset-links",
        json={"asset_id": paper_asset["asset_id"], "task_id": task_id},
        headers=admin_headers,
    )
    assert link_response.status_code == 201, link_response.text
    assert link_response.json()["asset"]["reference_id"] == seeded["paper_id"]
    duplicate_link = await test_client.post(
        f"/api/research/projects/{project_id}/plan/asset-links",
        json={"asset_id": paper_asset["asset_id"], "task_id": task_id},
        headers=admin_headers,
    )
    assert duplicate_link.status_code == 409
    assert duplicate_link.json()["detail"]["error"] == "plan_asset_already_linked"

    plan_response = await test_client.get(
        f"/api/research/projects/{project_id}/plan",
        headers=admin_headers,
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["summary"]["progress"] == 0
    assert plan["summary"]["progress_source"] == "tasks"
    assert plan["milestones"][0]["tasks"][0]["asset_links"][0]["asset"]["available"] is True

    blocked_milestone_delete = await test_client.delete(
        f"/api/research/projects/{project_id}/milestones/{milestone_id}",
        headers=admin_headers,
    )
    assert blocked_milestone_delete.status_code == 409
    assert blocked_milestone_delete.json()["detail"]["error"] == "milestone_has_tasks"

    detail_response = await test_client.get(f"/api/research/projects/{project_id}", headers=admin_headers)
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["asset_counts"]["total"] == 5
    activity_types = [item["activity_type"] for item in detail["activities"]]
    assert activity_types.count("asset_added") == 5
    assert "project_created" in activity_types

    async with _temporary_superadmin(test_client) as other_admin:
        isolated_response = await test_client.get(
            f"/api/research/projects/{project_id}",
            headers=other_admin["headers"],
        )
        assert isolated_response.status_code == 404

    update_response = await test_client.patch(
        f"/api/research/projects/{project_id}",
        json={"progress": 75, "next_action": "Validate the linked synthesis", "tags": ["evidence", "validated"]},
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["progress"] == 0
    assert update_response.json()["progress_source"] == "tasks"

    blocked_complete_response = await test_client.patch(
        f"/api/research/projects/{project_id}",
        json={"status": "completed"},
        headers=admin_headers,
    )
    assert blocked_complete_response.status_code == 409
    assert blocked_complete_response.json()["detail"]["error"] == "project_has_open_tasks"

    task_done_response = await test_client.patch(
        f"/api/research/projects/{project_id}/tasks/{task_id}",
        json={"status": "done"},
        headers=admin_headers,
    )
    assert task_done_response.status_code == 200, task_done_response.text
    assert task_done_response.json()["completed_at"] is not None

    completed_plan_response = await test_client.get(
        f"/api/research/projects/{project_id}/plan",
        headers=admin_headers,
    )
    assert completed_plan_response.status_code == 200, completed_plan_response.text
    assert completed_plan_response.json()["summary"]["progress"] == 100
    assert completed_plan_response.json()["milestones"][0]["status"] == "completed"

    complete_response = await test_client.patch(
        f"/api/research/projects/{project_id}",
        json={"status": "completed"},
        headers=admin_headers,
    )
    assert complete_response.status_code == 200, complete_response.text
    assert complete_response.json()["progress"] == 100
    assert complete_response.json()["completed_at"] is not None

    markdown_report = await test_client.get(
        f"/api/research/projects/{project_id}/report",
        params={"format": "markdown"},
        headers=admin_headers,
    )
    assert markdown_report.status_code == 200, markdown_report.text
    report_text = markdown_report.content.decode("utf-8")
    assert "Validate the evidence base" in report_text
    assert "Verify the strongest source" in report_text
    assert "Evidence-grounded Research Opportunity Discovery" in report_text

    docx_report = await test_client.get(
        f"/api/research/projects/{project_id}/report",
        params={"format": "docx"},
        headers=admin_headers,
    )
    assert docx_report.status_code == 200, docx_report.text
    assert docx_report.content.startswith(b"PK")

    read_only_response = await test_client.delete(
        f"/api/research/projects/{project_id}/assets/{paper_asset['asset_id']}",
        headers=admin_headers,
    )
    assert read_only_response.status_code == 409
    assert read_only_response.json()["detail"]["error"] == "project_read_only"

    archive_response = await test_client.patch(
        f"/api/research/projects/{project_id}",
        json={"status": "archived"},
        headers=admin_headers,
    )
    assert archive_response.status_code == 200, archive_response.text
    blocked_edit = await test_client.patch(
        f"/api/research/projects/{project_id}",
        json={"title": "Archived projects are read only"},
        headers=admin_headers,
    )
    assert blocked_edit.status_code == 409
    assert blocked_edit.json()["detail"]["error"] == "project_archived"

    reactivate_response = await test_client.patch(
        f"/api/research/projects/{project_id}",
        json={"status": "active", "progress": 80},
        headers=admin_headers,
    )
    assert reactivate_response.status_code == 200, reactivate_response.text
    assert reactivate_response.json()["completed_at"] is None
    assert reactivate_response.json()["archived_at"] is None

    async with pg_manager.get_async_session_context() as session:
        paper = await session.scalar(
            select(AcademicPaper).where(
                AcademicPaper.kb_id == kb_id,
                AcademicPaper.paper_id == seeded["paper_id"],
            )
        )
        assert paper is not None
        await session.delete(paper)

    notes_response = await test_client.patch(
        f"/api/research/projects/{project_id}/assets/{paper_asset['asset_id']}",
        json={"notes": "Source removed; retain this snapshot for provenance"},
        headers=admin_headers,
    )
    assert notes_response.status_code == 200, notes_response.text
    assert notes_response.json()["available"] is False
    assert notes_response.json()["title"] == "Evidence-grounded Research Opportunity Discovery"

    stale_report = await test_client.get(
        f"/api/research/projects/{project_id}/report",
        params={"format": "markdown"},
        headers=admin_headers,
    )
    assert stale_report.status_code == 200, stale_report.text
    assert "源成果已失效" in stale_report.content.decode("utf-8")

    delete_response = await test_client.delete(f"/api/research/projects/{project_id}", headers=admin_headers)
    assert delete_response.status_code == 204, delete_response.text
    missing_response = await test_client.get(f"/api/research/projects/{project_id}", headers=admin_headers)
    assert missing_response.status_code == 404

    source_response = await test_client.get(
        f"/api/research/synthesis-runs/{synthesis_run_id}",
        headers=admin_headers,
    )
    assert source_response.status_code == 200, source_response.text


async def _assert_active_synthesis_unique_constraint(*, kb_id: str, uid: str) -> None:
    constraint_uid = f"{uid}_constraint"
    with pytest.raises(IntegrityError):
        async with pg_manager.get_async_session_context() as session:
            session.add(
                ResearchSynthesisRun(
                    run_id=f"active_{uuid.uuid4().hex[:12]}",
                    kb_id=kb_id,
                    uid=constraint_uid,
                    raw_query="First active synthesis",
                    model_config_json={},
                    retrieval_config={},
                    active_key="active",
                    status="pending",
                    stage="pending",
                    stage_timings={},
                )
            )
            await session.flush()
            session.add(
                ResearchSynthesisRun(
                    run_id=f"active_{uuid.uuid4().hex[:12]}",
                    kb_id=kb_id,
                    uid=constraint_uid,
                    raw_query="Second active synthesis",
                    model_config_json={},
                    retrieval_config={},
                    active_key="active",
                    status="pending",
                    stage="pending",
                    stage_timings={},
                )
            )
            await session.flush()
