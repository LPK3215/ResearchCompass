"""research_router 路由顺序守卫。

字面量路径与同层参数路由使用相同方法时，先注册的参数路由会吞掉字面量路径
（例如 ``/papers/cards`` 被 ``/papers/{paper_id}`` 匹配成 paper_id=cards）。
这里对研究域路由做静态检查，新增字面量接口时保持声明在参数路由之前。
"""

from server.routers.research_router import research


def _route_entries() -> list[tuple[str, list[str], str]]:
    entries: list[tuple[str, list[str], str]] = []
    for route in research.routes:
        for method in getattr(route, "methods", None) or set():
            entries.append((method, route.path.split("/"), route.path))
    return entries


def test_no_literal_route_is_shadowed_by_param_route():
    entries = _route_entries()
    shadowed: list[tuple[str, str, str]] = []
    for index, (method, segments, path) in enumerate(entries):
        for earlier_method, earlier_segments, earlier_path in entries[:index]:
            if method != earlier_method or len(segments) != len(earlier_segments):
                continue
            differences = [
                (current, earlier) for current, earlier in zip(segments, earlier_segments) if current != earlier
            ]
            if len(differences) != 1:
                continue
            current, earlier = differences[0]
            if earlier.startswith("{") and not current.startswith("{"):
                shadowed.append((method, path, earlier_path))

    assert shadowed == []
