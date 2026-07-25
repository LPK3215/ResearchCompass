"""任务中心必须是系统级超级管理员能力。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from server.routers.system_task_router import tasks
from server.utils.auth_middleware import get_superadmin_user


def test_all_task_routes_require_superadmin_dependency():
    routes = [route for route in tasks.routes if isinstance(route, APIRoute)]

    assert len(routes) == 4
    for route in routes:
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert get_superadmin_user in dependency_calls


async def test_department_admin_cannot_use_task_center():
    with pytest.raises(HTTPException) as exc_info:
        await get_superadmin_user(SimpleNamespace(role="admin"))

    assert exc_info.value.status_code == 403
