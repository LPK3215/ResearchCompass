from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import server.routers.auth_router as auth_router


class _DepartmentRepoStub:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    async def list_departments(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=index + 1, name=name) for index, name in enumerate(self._names)]


@pytest.mark.asyncio
async def test_resolve_default_department_returns_its_id(monkeypatch) -> None:
    monkeypatch.setattr(auth_router, "DepartmentRepository", lambda: _DepartmentRepoStub(["研发部", "默认部门"]))

    assert await auth_router._resolve_default_department_id() == 2


@pytest.mark.asyncio
async def test_resolve_default_department_fails_instead_of_creating_a_broken_account(monkeypatch) -> None:
    """缺默认部门时必须显式失败：否则建出的账号能登录但所有接口 400。"""
    monkeypatch.setattr(auth_router, "DepartmentRepository", lambda: _DepartmentRepoStub(["研发部", "产品部"]))

    with pytest.raises(HTTPException) as exc_info:
        await auth_router._resolve_default_department_id()

    assert exc_info.value.status_code == 409
    assert "默认部门" in exc_info.value.detail
