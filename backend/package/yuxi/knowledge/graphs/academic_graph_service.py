"""ResearchCompass 学术引用图谱 Neo4j 投影服务。

本模块是本仓库在开源智能体框架 Yuxi 的 Neo4j 存储基础设施之上实现的论文引用图谱服务，
负责将论文、作者、主题与 CITES 引用边投影到 Neo4j，并提供两跳关联解释与知识库投影清理；
通用 Neo4j 连接池与事务封装由 Yuxi 提供。
"""

from __future__ import annotations

import asyncio
from typing import Any

from yuxi.storage.neo4j import get_shared_neo4j_connection, neo4j_read, neo4j_write, safe_neo4j_label


class AcademicGraphService:
    @property
    def driver(self):
        return get_shared_neo4j_connection().driver

    async def project_paper(
        self,
        *,
        kb_id: str,
        paper: dict[str, Any],
        authors: list[dict[str, Any]],
        topics: list[dict[str, Any]],
    ) -> None:
        label = safe_neo4j_label(kb_id)

        def write(tx):
            tx.run(
                f"""
                MERGE (p:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id, graph_paper_id: $graph_paper_id}})
                SET p.title = $title,
                    p.publication_year = $publication_year,
                    p.venue = $venue,
                    p.citation_count = $citation_count,
                    p.semantic_scholar_id = $semantic_scholar_id,
                    p.is_library_paper = $is_library_paper,
                    p.academic_paper_id = $academic_paper_id,
                    p.source = $source
                """,
                **paper,
            )
            for author_order, author in enumerate(authors):
                tx.run(
                    f"""
                    MATCH (p:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id, graph_paper_id: $graph_paper_id}})
                    MERGE (a:Author:AcademicGraph:`{label}` {{kb_id: $kb_id, author_id: $author_id}})
                    SET a.name = $name, a.semantic_scholar_id = $semantic_scholar_id
                    MERGE (a)-[r:AUTHORED {{kb_id: $kb_id}}]->(p)
                    SET r.author_order = $author_order
                    """,
                    kb_id=kb_id,
                    graph_paper_id=paper["graph_paper_id"],
                    author_id=author["author_id"],
                    name=author["name"],
                    semantic_scholar_id=author.get("semantic_scholar_id"),
                    author_order=author_order,
                )
            for topic in topics:
                tx.run(
                    f"""
                    MATCH (p:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id, graph_paper_id: $graph_paper_id}})
                    MERGE (t:Topic:AcademicGraph:`{label}` {{kb_id: $kb_id, topic_id: $topic_id}})
                    SET t.name = $name, t.category = $category
                    MERGE (p)-[:HAS_TOPIC {{kb_id: $kb_id}}]->(t)
                    """,
                    kb_id=kb_id,
                    graph_paper_id=paper["graph_paper_id"],
                    topic_id=topic["topic_id"],
                    name=topic["name"],
                    category=topic.get("category"),
                )

        await asyncio.to_thread(neo4j_write, self.driver, write)

    async def project_citation(self, *, kb_id: str, citation: dict[str, Any]) -> None:
        label = safe_neo4j_label(kb_id)

        def write(tx):
            tx.run(
                f"""
                MATCH (source:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id, graph_paper_id: $citing_paper_id}})
                MATCH (target:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id, graph_paper_id: $cited_paper_id}})
                MERGE (source)-[r:CITES {{kb_id: $kb_id, citation_id: $citation_id}}]->(target)
                SET r.contexts = $contexts,
                    r.intents = $intents,
                    r.is_influential = $is_influential,
                    r.source = $source
                """,
                **citation,
            )

        await asyncio.to_thread(neo4j_write, self.driver, write)

    async def get_network(
        self, *, kb_id: str, center_paper_id: str | None = None, depth: int = 1, limit: int = 200
    ) -> dict[str, Any]:
        label = safe_neo4j_label(kb_id)
        depth = min(max(int(depth), 1), 3)
        limit = min(max(int(limit), 1), 500)
        center_clause = "{kb_id: $kb_id, graph_paper_id: $center_paper_id}" if center_paper_id else "{kb_id: $kb_id}"
        cypher = f"""
        MATCH (center:Paper:AcademicGraph:`{label}` {center_clause})
        MATCH path=(center)-[:CITES*0..{depth}]-(paper:Paper:AcademicGraph:`{label}`)
        WITH collect(DISTINCT paper)[0..$limit] AS papers
        UNWIND papers AS paper
        OPTIONAL MATCH (paper)-[citation:CITES]->(target:Paper:AcademicGraph:`{label}`)
        WHERE target IN papers
        OPTIONAL MATCH (author:Author:AcademicGraph:`{label}`)-[authored:AUTHORED]->(paper)
        OPTIONAL MATCH (paper)-[:HAS_TOPIC]->(topic:Topic:AcademicGraph:`{label}`)
        RETURN properties(paper) AS paper,
               collect(DISTINCT properties(author)) AS authors,
               collect(DISTINCT properties(topic)) AS topics,
               collect(DISTINCT {{citation: properties(citation), target_id: target.graph_paper_id}}) AS outgoing
        """
        rows = await asyncio.to_thread(
            neo4j_read,
            self.driver,
            cypher,
            kb_id=kb_id,
            center_paper_id=center_paper_id,
            limit=limit,
        )
        return self._serialize_network(rows)

    async def get_status(self, *, kb_id: str) -> dict[str, int]:
        label = safe_neo4j_label(kb_id)
        rows = await asyncio.to_thread(
            neo4j_read,
            self.driver,
            f"""
            MATCH (paper:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id}})
            WITH count(paper) AS papers
            OPTIONAL MATCH (:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id}})
                -[citation:CITES {{kb_id: $kb_id}}]->
                (:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id}})
            RETURN papers, count(citation) AS citations
            """,
            kb_id=kb_id,
        )
        if not rows:
            return {"papers": 0, "citations": 0}
        return {
            "papers": int(rows[0].get("papers") or 0),
            "citations": int(rows[0].get("citations") or 0),
        }

    async def expand_papers_by_ppr(
        self,
        *,
        kb_id: str,
        seed_weights: dict[str, float],
        depth: int,
        max_nodes: int,
        top_k: int,
        damping: float,
    ) -> dict[str, Any]:
        if not seed_weights:
            raise ValueError("论文引用图谱 PPR 缺少种子论文")
        label = safe_neo4j_label(kb_id)
        depth = min(max(int(depth), 1), 3)
        max_nodes = min(max(int(max_nodes), 1), 5000)
        rows = await asyncio.to_thread(
            neo4j_read,
            self.driver,
            f"""
            MATCH (seed:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id}})
            WHERE seed.graph_paper_id IN $seed_ids
            MATCH (seed)-[:CITES*0..{depth}]-(paper:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id}})
            WITH collect(DISTINCT paper)[0..$max_nodes] AS papers
            UNWIND papers AS source
            OPTIONAL MATCH (source)-[citation:CITES {{kb_id: $kb_id}}]->
                (target:Paper:AcademicGraph:`{label}` {{kb_id: $kb_id}})
            WHERE target IN papers
            WITH papers,
                 collect(DISTINCT CASE WHEN citation IS NULL THEN null ELSE {{
                     source_id: source.graph_paper_id,
                     target_id: target.graph_paper_id,
                     citation_id: citation.citation_id,
                     is_influential: citation.is_influential
                 }} END) AS citations
            RETURN [paper IN papers | properties(paper)] AS papers, citations
            """,
            kb_id=kb_id,
            seed_ids=list(seed_weights),
            max_nodes=max_nodes,
        )
        if not rows:
            raise ValueError("论文引用图谱中不存在检索命中的种子论文")
        papers = [dict(item) for item in rows[0].get("papers") or []]
        citations = [dict(item) for item in rows[0].get("citations") or [] if item]
        return self.rank_papers_by_ppr(
            papers=papers,
            citations=citations,
            seed_weights=seed_weights,
            top_k=top_k,
            damping=damping,
        )

    @staticmethod
    def rank_papers_by_ppr(
        *,
        papers: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        seed_weights: dict[str, float],
        top_k: int,
        damping: float,
    ) -> dict[str, Any]:
        try:
            import igraph as ig
        except ImportError as exc:
            raise RuntimeError("论文引用图谱 PPR 需要 python-igraph") from exc

        paper_by_id = {str(paper.get("graph_paper_id")): paper for paper in papers if paper.get("graph_paper_id")}
        missing_seed_ids = sorted(set(seed_weights) - set(paper_by_id))
        if missing_seed_ids:
            raise ValueError("论文引用图谱缺少部分检索种子")
        if not citations:
            raise ValueError("论文引用图谱没有可用于 PPR 的 CITES 关系")

        paper_ids = list(paper_by_id)
        index_by_id = {paper_id: index for index, paper_id in enumerate(paper_ids)}
        edge_pairs: list[tuple[int, int]] = []
        citation_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        for citation in citations:
            source_id = str(citation.get("source_id") or "")
            target_id = str(citation.get("target_id") or "")
            if source_id not in index_by_id or target_id not in index_by_id:
                continue
            edge_pairs.append((index_by_id[source_id], index_by_id[target_id]))
            citation_by_pair[(source_id, target_id)] = citation
        if not edge_pairs:
            raise ValueError("论文引用图谱子图没有有效 CITES 关系")

        # 这里是“引用关联扩展”而非有向影响力 PageRank：检索结果既要能沿引用
        # 方向找到参考文献，也要能反向找到后续引用论文，因此使用无向关系图。
        # retrieval_config 会显式标记该口径，避免把分数宣传为标准有向 PageRank。
        graph = ig.Graph(n=len(paper_ids), edges=edge_pairs, directed=False)
        reset = [max(float(seed_weights.get(paper_id, 0.0)), 0.0) for paper_id in paper_ids]
        reset_total = sum(reset)
        if reset_total <= 0:
            raise ValueError("论文引用图谱种子权重必须大于零")
        reset = [value / reset_total for value in reset]
        scores = graph.personalized_pagerank(
            damping=min(max(float(damping), 0.1), 0.99),
            reset=reset,
        )

        seed_ids = list(seed_weights)
        seed_indexes = [index_by_id[seed_id] for seed_id in seed_ids]
        distances = graph.distances(source=seed_indexes)
        ranked: list[dict[str, Any]] = []
        for paper_id, index in index_by_id.items():
            reachable = [
                (distances[seed_index][index], seed_ids[seed_index])
                for seed_index in range(len(seed_ids))
                if distances[seed_index][index] != float("inf")
            ]
            if not reachable:
                continue
            _, path_seed_id = min(
                reachable,
                key=lambda item: (item[0], -float(seed_weights[item[1]]), item[1]),
            )
            vertex_path = graph.get_shortest_paths(
                index_by_id[path_seed_id],
                to=index,
                output="vpath",
            )[0]
            path_ids = [paper_ids[vertex_index] for vertex_index in vertex_path]
            path_edges = []
            for source_id, target_id in zip(path_ids, path_ids[1:]):
                citation = citation_by_pair.get((source_id, target_id))
                direction = "outgoing"
                if citation is None:
                    citation = citation_by_pair[(target_id, source_id)]
                    direction = "incoming"
                path_edges.append(
                    {
                        "source": source_id,
                        "target": target_id,
                        "direction": direction,
                        "citation_id": citation.get("citation_id"),
                        "is_influential": bool(citation.get("is_influential")),
                    }
                )
            ranked.append(
                {
                    **paper_by_id[paper_id],
                    "graph_paper_id": paper_id,
                    "graph_score": float(scores[index]),
                    "is_seed": paper_id in seed_weights,
                    "path": {
                        "seed_graph_paper_id": path_seed_id,
                        "nodes": [
                            {
                                "graph_paper_id": path_id,
                                "title": paper_by_id[path_id].get("title"),
                            }
                            for path_id in path_ids
                        ],
                        "edges": path_edges,
                    },
                }
            )
        ranked.sort(key=lambda item: (-item["graph_score"], item["graph_paper_id"]))
        return {
            "papers": ranked[: max(int(top_k), 1)],
            "paper_scores": {item["graph_paper_id"]: item["graph_score"] for item in ranked},
            "node_count": len(paper_ids),
            "citation_count": len(edge_pairs),
        }

    @staticmethod
    def _serialize_network(rows: list[dict[str, Any]]) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        for row in rows:
            paper = dict(row.get("paper") or {})
            paper_id = str(paper.get("graph_paper_id") or "")
            if not paper_id:
                continue
            nodes[paper_id] = {"id": paper_id, "type": "paper", **paper}
            for author_value in row.get("authors") or []:
                author = dict(author_value or {})
                author_id = str(author.get("author_id") or "")
                if not author_id:
                    continue
                nodes[author_id] = {"id": author_id, "type": "author", **author}
                edge_id = f"{author_id}:AUTHORED:{paper_id}"
                edges[edge_id] = {"id": edge_id, "source": author_id, "target": paper_id, "type": "AUTHORED"}
            for topic_value in row.get("topics") or []:
                topic = dict(topic_value or {})
                topic_id = str(topic.get("topic_id") or "")
                if not topic_id:
                    continue
                nodes[topic_id] = {"id": topic_id, "type": "topic", **topic}
                edge_id = f"{paper_id}:HAS_TOPIC:{topic_id}"
                edges[edge_id] = {"id": edge_id, "source": paper_id, "target": topic_id, "type": "HAS_TOPIC"}
            for outgoing in row.get("outgoing") or []:
                citation = dict(outgoing.get("citation") or {})
                target_id = str(outgoing.get("target_id") or "")
                citation_id = str(citation.get("citation_id") or "")
                if citation_id and target_id:
                    edges[citation_id] = {
                        "id": citation_id,
                        "source": paper_id,
                        "target": target_id,
                        "type": "CITES",
                        **citation,
                    }
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    async def delete_kb_projection(self, kb_id: str) -> None:
        label = safe_neo4j_label(kb_id)
        cypher = f"MATCH (n:AcademicGraph:`{label}` {{kb_id: $kb_id}}) DETACH DELETE n RETURN count(n) AS deleted"

        def write(tx):
            tx.run(cypher, kb_id=kb_id)

        await asyncio.to_thread(neo4j_write, self.driver, write)


__all__ = ["AcademicGraphService"]
