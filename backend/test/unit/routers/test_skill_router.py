from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.routers import skill_router
from server.routers.skill_router import skills, user_skills
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.storage.postgres.models_business import Skill, User


SECRET = "Authorization=secret-api-key path=/private/repository"


class _CapturingLogger:
    def __init__(self):
        self.entries: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, level: str):
        def record(*args, **kwargs):
            self.entries.append((level, args, kwargs))

        return record


def _build_app(*, role: str = "admin") -> FastAPI:
    app = FastAPI()
    app.include_router(skills, prefix="/api")
    app.include_router(user_skills, prefix="/api")

    async def fake_db():
        return None

    async def fake_required_user():
        return User(
            username=role,
            uid=role,
            password_hash="x",
            role=role,
            department_id=1,
        )

    async def fake_admin_user():
        if role not in {"admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return await fake_required_user()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_required_user] = fake_required_user
    app.dependency_overrides[get_admin_user] = fake_admin_user
    return app


def _skill(
    slug: str = "demo",
    *,
    source_type: str = "upload",
    created_by: str = "admin",
    enabled: bool = True,
    user_uids: list[str] | None = None,
) -> Skill:
    return Skill(
        slug=slug,
        name=slug,
        description="demo skill",
        source_type=source_type,
        dir_path=f"skills/{slug}",
        share_config={"access_level": "user", "department_ids": [], "user_uids": user_uids or [created_by]},
        enabled=enabled,
        created_by=created_by,
        updated_by=created_by,
    )


def test_list_visible_skills_route_returns_allowed_levels_and_can_manage(monkeypatch):
    async def fake_list_visible_skills_for_management(_db, user):
        assert user.uid == "admin"
        return [_skill()]

    monkeypatch.setattr(
        "server.routers.skill_router.list_visible_skills_for_management",
        fake_list_visible_skills_for_management,
    )

    client = TestClient(_build_app())
    resp = client.get("/api/system/skills")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"][0]["slug"] == "demo"
    assert payload["data"][0]["can_manage"] is True
    assert payload["allowed_access_levels"] == ["global", "department", "user"]


def test_list_visible_skills_route_allows_normal_user_readonly_items(monkeypatch):
    async def fake_list_visible_skills_for_management(_db, user):
        assert user.uid == "user"
        return [
            _skill(slug="owned-disabled", created_by="user", enabled=False),
            _skill(slug="shared", created_by="other", user_uids=["user"]),
        ]

    monkeypatch.setattr(
        "server.routers.skill_router.list_visible_skills_for_management",
        fake_list_visible_skills_for_management,
    )

    client = TestClient(_build_app(role="user"))
    resp = client.get("/api/system/skills")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert [(item["slug"], item["can_manage"]) for item in payload["data"]] == [
        ("owned-disabled", True),
        ("shared", False),
    ]
    assert payload["allowed_access_levels"] == ["user"]


def test_list_accessible_skills_route(monkeypatch):
    async def fake_list_accessible_skills(_db, user):
        assert user.uid == "user"
        return [_skill(created_by="user")]

    monkeypatch.setattr("server.routers.skill_router.list_accessible_skills", fake_list_accessible_skills)

    client = TestClient(_build_app(role="user"))
    resp = client.get("/api/skills/accessible")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"][0]["slug"] == "demo"
    assert payload["data"][0]["can_manage"] is True


def test_prepare_skill_upload_route(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_prepare_skill_upload(_db, *, filename, file_bytes, operator):
        captured["filename"] = filename
        captured["file_bytes"] = file_bytes.decode("utf-8")
        captured["operator_uid"] = operator.uid
        return {"draft_id": "draft-1", "items": [{"slug": "demo", "success": True}]}

    monkeypatch.setattr("server.routers.skill_router.prepare_skill_upload", fake_prepare_skill_upload)

    client = TestClient(_build_app(role="user"))
    resp = client.post(
        "/api/skills/import/prepare",
        files={
            "file": (
                r"C:\Users\alice\private\SKILL.md",
                b"---\nname: demo\ndescription: demo skill\n---\n",
                "text/markdown",
            )
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["draft_id"] == "draft-1"
    assert captured == {
        "filename": "SKILL.md",
        "file_bytes": "---\nname: demo\ndescription: demo skill\n---\n",
        "operator_uid": "user",
    }


@pytest.mark.parametrize(
    "source",
    [
        "/var/lib/yuxi/skills",
        "file:///var/lib/yuxi/skills",
        "http://github.com/anthropics/skills",
        "https://token@github.com/anthropics/skills",
        "https://github.com/anthropics/skills?token=secret",
    ],
)
def test_remote_skill_routes_reject_local_or_credentialed_sources(monkeypatch, source):
    async def unexpected_list(_source):
        raise AssertionError("remote list must not run")

    async def unexpected_prepare(*_args, **_kwargs):
        raise AssertionError("remote prepare must not run")

    monkeypatch.setattr(skill_router, "list_remote_skills", unexpected_list)
    monkeypatch.setattr(skill_router, "prepare_remote_skill_install", unexpected_prepare)

    client = TestClient(_build_app(role="user"))
    list_resp = client.post("/api/skills/remote/list", json={"source": source})
    prepare_resp = client.post(
        "/api/skills/remote/prepare",
        json={"source": source, "skills": ["demo"]},
    )

    assert list_resp.status_code == 400
    assert list_resp.json() == {"detail": "Skill 请求无效"}
    assert prepare_resp.status_code == 400
    assert prepare_resp.json() == {"detail": "Skill 请求无效"}
    assert source not in list_resp.text
    assert source not in prepare_resp.text


def test_remote_skill_list_accepts_github_https_url(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_list_remote_skills(source):
        captured["source"] = source
        return [{"name": "demo", "description": "Demo"}]

    monkeypatch.setattr(skill_router, "list_remote_skills", fake_list_remote_skills)

    client = TestClient(_build_app(role="user"))
    resp = client.post(
        "/api/skills/remote/list",
        json={"source": "https://github.com/anthropics/skills.git"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == [{"name": "demo", "description": "Demo"}]
    assert captured == {"source": "https://github.com/anthropics/skills.git"}


def test_remote_skill_failure_does_not_expose_exception_or_source(monkeypatch):
    logger = _CapturingLogger()
    source = "private-owner/private-repository"

    async def fail(_source):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(skill_router, "list_remote_skills", fail)
    monkeypatch.setattr(skill_router, "logger", logger)

    client = TestClient(_build_app(role="user"))
    resp = client.post("/api/skills/remote/list", json={"source": source})

    assert resp.status_code == 500
    assert resp.json() == {"detail": "获取远程 skills 列表失败"}
    assert SECRET not in resp.text
    assert SECRET not in repr(logger.entries)
    assert source not in repr(logger.entries)


def test_remote_skill_value_error_is_sanitized(monkeypatch):
    async def fail(_source):
        raise ValueError(SECRET)

    monkeypatch.setattr(skill_router, "list_remote_skills", fail)

    client = TestClient(_build_app(role="user"))
    resp = client.post("/api/skills/remote/list", json={"source": "anthropics/skills"})

    assert resp.status_code == 400
    assert resp.json() == {"detail": "Skill 请求无效"}
    assert SECRET not in resp.text


def test_remote_skill_prepare_sanitizes_item_failures(monkeypatch):
    async def fake_prepare_remote_skill_install(*_args, **_kwargs):
        return {
            "draft_id": "draft-1",
            "source": "anthropics/skills",
            "items": [{"slug": "demo", "success": False, "error": SECRET}],
        }

    monkeypatch.setattr(skill_router, "prepare_remote_skill_install", fake_prepare_remote_skill_install)

    client = TestClient(_build_app(role="user"))
    resp = client.post(
        "/api/skills/remote/prepare",
        json={"source": "anthropics/skills", "skills": ["demo"]},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["items"] == [
        {"slug": "demo", "success": False, "error": "远程 Skill 解析失败"}
    ]
    assert SECRET not in resp.text


def test_remote_skill_search_failure_does_not_expose_query(monkeypatch):
    logger = _CapturingLogger()
    query = "private research query"

    async def fail(_query):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(skill_router, "search_remote_skills", fail)
    monkeypatch.setattr(skill_router, "logger", logger)

    client = TestClient(_build_app(role="user"))
    resp = client.post("/api/skills/remote/search", json={"query": query})

    assert resp.status_code == 500
    assert resp.json() == {"detail": "搜索远程 skills 失败"}
    assert SECRET not in resp.text
    assert SECRET not in repr(logger.entries)
    assert query not in repr(logger.entries)


def test_skill_file_failure_does_not_expose_path(monkeypatch):
    logger = _CapturingLogger()
    requested_path = "private/credential.txt"

    async def allow_read(_db, _user, slug):
        return _skill(slug=slug)

    async def fail_read(_db, _slug, _path):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(skill_router, "get_management_readable_skill_or_raise", allow_read)
    monkeypatch.setattr(skill_router, "read_skill_file", fail_read)
    monkeypatch.setattr(skill_router, "logger", logger)

    client = TestClient(_build_app(role="user"))
    resp = client.get("/api/system/skills/demo/file", params={"path": requested_path})

    assert resp.status_code == 500
    assert resp.json() == {"detail": "读取技能文件失败"}
    assert SECRET not in resp.text
    assert SECRET not in repr(logger.entries)
    assert requested_path not in repr(logger.entries)


def test_skill_file_route_stops_when_management_read_is_denied(monkeypatch):
    async def deny_read(_db, _user, _slug):
        raise ValueError(f"技能不存在或无权访问: {SECRET}")

    async def unexpected_read(*_args, **_kwargs):
        raise AssertionError("file read must not run")

    monkeypatch.setattr(skill_router, "get_management_readable_skill_or_raise", deny_read)
    monkeypatch.setattr(skill_router, "read_skill_file", unexpected_read)

    client = TestClient(_build_app(role="user"))
    resp = client.get("/api/system/skills/demo/file", params={"path": "SKILL.md"})

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Skill 不存在或无权访问"}
    assert SECRET not in resp.text


def test_batch_operation_failure_details_are_sanitized(monkeypatch):
    async def fake_confirm(*_args, **_kwargs):
        return [{"slug": "demo", "success": False, "error": SECRET}]

    async def allow_manage(_db, _user, slug):
        return _skill(slug=slug)

    async def fake_delete_batch(_db, *, slugs):
        return [{"slug": slugs[0], "success": False, "error": SECRET}]

    monkeypatch.setattr(skill_router, "confirm_skill_install_draft", fake_confirm)
    monkeypatch.setattr(skill_router, "get_manageable_skill_or_raise", allow_manage)
    monkeypatch.setattr(skill_router, "delete_skills_batch", fake_delete_batch)

    client = TestClient(_build_app(role="user"))
    confirm_resp = client.post(
        "/api/skills/install-drafts/draft-1/confirm",
        json={"share_config": None},
    )
    delete_resp = client.post("/api/system/skills/delete-batch", json={"slugs": ["demo"]})

    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["data"] == [{"slug": "demo", "success": False, "error": "Skill 安装失败"}]
    assert delete_resp.status_code == 200, delete_resp.text
    assert delete_resp.json()["data"] == [{"slug": "demo", "success": False, "error": "删除 Skill 失败"}]
    assert SECRET not in confirm_resp.text
    assert SECRET not in delete_resp.text


def test_remote_skill_prepare_and_confirm_routes(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_prepare_remote_skill_install(_db, *, source, skills, operator):
        captured["prepare"] = {"source": source, "skills": skills, "operator_uid": operator.uid}
        return {"draft_id": "draft-remote", "items": [{"slug": "frontend-design", "success": True}]}

    async def fake_confirm_skill_install_draft(_db, *, draft_id, share_config, operator):
        captured["confirm"] = {"draft_id": draft_id, "share_config": share_config, "operator_uid": operator.uid}
        return [{"slug": "frontend-design", "success": True}]

    monkeypatch.setattr("server.routers.skill_router.prepare_remote_skill_install", fake_prepare_remote_skill_install)
    monkeypatch.setattr("server.routers.skill_router.confirm_skill_install_draft", fake_confirm_skill_install_draft)

    client = TestClient(_build_app(role="user"))
    prepare_resp = client.post(
        "/api/skills/remote/prepare",
        json={"source": "anthropics/skills", "skills": ["frontend-design"]},
    )
    confirm_resp = client.post(
        "/api/skills/install-drafts/draft-remote/confirm",
        json={"share_config": {"access_level": "user", "department_ids": [], "user_uids": ["user"]}},
    )

    assert prepare_resp.status_code == 200, prepare_resp.text
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert captured["prepare"] == {
        "source": "anthropics/skills",
        "skills": ["frontend-design"],
        "operator_uid": "user",
    }
    assert captured["confirm"]["draft_id"] == "draft-remote"
    assert captured["confirm"]["operator_uid"] == "user"


def test_discard_skill_draft_route(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_discard_skill_install_draft(*, draft_id, operator):
        captured["draft_id"] = draft_id
        captured["operator_uid"] = operator.uid

    monkeypatch.setattr("server.routers.skill_router.discard_skill_install_draft", fake_discard_skill_install_draft)

    client = TestClient(_build_app(role="user"))
    resp = client.delete("/api/skills/install-drafts/draft-1")

    assert resp.status_code == 200, resp.text
    assert captured == {"draft_id": "draft-1", "operator_uid": "user"}


def test_dependency_options_route_checks_manage_permission(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_get_manageable_skill_or_raise(_db, user, slug):
        captured["manageable"] = {"slug": slug, "operator_uid": user.uid}
        return _skill(slug=slug)

    async def fake_get_skill_dependency_options(_db, user, slug=None):
        captured["options"] = {"slug": slug, "operator_uid": user.uid}
        return {"tools": [{"slug": "calculator", "name": "Calculator"}], "mcps": ["mcp-a"], "skills": ["other"]}

    monkeypatch.setattr("server.routers.skill_router.get_manageable_skill_or_raise", fake_get_manageable_skill_or_raise)
    monkeypatch.setattr("server.routers.skill_router.get_skill_dependency_options", fake_get_skill_dependency_options)

    client = TestClient(_build_app())
    resp = client.get("/api/system/skills/dependency-options?slug=demo")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["skills"] == ["other"]
    assert captured["manageable"] == {"slug": "demo", "operator_uid": "admin"}
    assert captured["options"] == {"slug": "demo", "operator_uid": "admin"}


def test_skill_tree_and_file_routes_check_management_read_permission(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_get_management_readable_skill_or_raise(_db, user, slug):
        captured.setdefault("read", []).append({"slug": slug, "operator_uid": user.uid})
        return _skill(slug=slug, created_by="user", enabled=False)

    async def fake_get_skill_tree(_db, slug):
        captured["tree_slug"] = slug
        return [{"name": "SKILL.md", "path": "SKILL.md", "is_dir": False}]

    async def fake_read_skill_file(_db, slug, path):
        captured["file"] = {"slug": slug, "path": path}
        return {"path": path, "content": "---\nname: demo\n---\n"}

    monkeypatch.setattr(
        "server.routers.skill_router.get_management_readable_skill_or_raise",
        fake_get_management_readable_skill_or_raise,
    )
    monkeypatch.setattr("server.routers.skill_router.get_skill_tree", fake_get_skill_tree)
    monkeypatch.setattr("server.routers.skill_router.read_skill_file", fake_read_skill_file)

    client = TestClient(_build_app(role="user"))
    tree_resp = client.get("/api/system/skills/demo/tree")
    file_resp = client.get("/api/system/skills/demo/file?path=SKILL.md")

    assert tree_resp.status_code == 200, tree_resp.text
    assert file_resp.status_code == 200, file_resp.text
    assert captured["read"] == [
        {"slug": "demo", "operator_uid": "user"},
        {"slug": "demo", "operator_uid": "user"},
    ]
    assert captured["tree_slug"] == "demo"
    assert captured["file"] == {"slug": "demo", "path": "SKILL.md"}


def test_skill_export_route_still_checks_manage_permission(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    export_path = tmp_path / "demo.zip"
    export_path.write_bytes(b"zip")

    async def fake_get_manageable_skill_or_raise(_db, user, slug):
        captured["manageable"] = {"slug": slug, "operator_uid": user.uid}
        return _skill(slug=slug)

    async def fake_export_skill_zip(_db, slug):
        captured["export_slug"] = slug
        return str(export_path), "demo.zip"

    monkeypatch.setattr("server.routers.skill_router.get_manageable_skill_or_raise", fake_get_manageable_skill_or_raise)
    monkeypatch.setattr("server.routers.skill_router.export_skill_zip", fake_export_skill_zip)

    client = TestClient(_build_app())
    resp = client.get("/api/system/skills/demo/export")

    assert resp.status_code == 200, resp.text
    assert captured["manageable"] == {"slug": "demo", "operator_uid": "admin"}
    assert captured["export_slug"] == "demo"


def test_update_skill_dependencies_route_passes_operator(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_update_skill_dependencies(
        _db,
        *,
        slug,
        tool_dependencies,
        mcp_dependencies,
        skill_dependencies,
        operator,
    ):
        captured["slug"] = slug
        captured["tool_dependencies"] = tool_dependencies
        captured["mcp_dependencies"] = mcp_dependencies
        captured["skill_dependencies"] = skill_dependencies
        captured["operator_uid"] = operator.uid
        return _skill(slug=slug)

    monkeypatch.setattr("server.routers.skill_router.update_skill_dependencies", fake_update_skill_dependencies)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/skills/demo/dependencies",
        json={
            "tool_dependencies": ["calculator"],
            "mcp_dependencies": ["mcp-a"],
            "skill_dependencies": ["other-skill"],
        },
    )

    assert resp.status_code == 200, resp.text
    assert captured == {
        "slug": "demo",
        "tool_dependencies": ["calculator"],
        "mcp_dependencies": ["mcp-a"],
        "skill_dependencies": ["other-skill"],
        "operator_uid": "admin",
    }


def test_builtin_routes_require_admin():
    client = TestClient(_build_app(role="user"))

    resp = client.get("/api/system/skills/builtin")

    assert resp.status_code == 403


def test_sync_builtin_skills_route(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_init_builtin_skills(_db, *, created_by):
        captured["created_by"] = created_by
        return [_skill(slug="builtin-demo", source_type="builtin")]

    monkeypatch.setattr("server.routers.skill_router.init_builtin_skills", fake_init_builtin_skills)

    client = TestClient(_build_app())
    resp = client.post("/api/system/skills/builtin/sync")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"][0]["slug"] == "builtin-demo"
    assert captured == {"created_by": "admin"}
