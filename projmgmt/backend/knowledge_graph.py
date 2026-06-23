from __future__ import annotations
from typing import Optional
import networkx as nx

from models import KGNode, KGEdge


class ProjectKG:
    def __init__(self):
        self.graph: nx.DiGraph = nx.DiGraph()

    def add_node(self, node: KGNode):
        self.graph.add_node(node.id, **node.model_dump())

    def add_edge(self, edge: KGEdge):
        self.graph.add_edge(edge.source, edge.target, **edge.model_dump())

    def has_node(self, node_id: str) -> bool:
        return node_id in self.graph.nodes

    def get_origin_nodes(self) -> list[dict]:
        return [d for _, d in self.graph.nodes(data=True) if d.get("plane") == "origin"]

    def get_user_nodes(self) -> list[dict]:
        return [d for _, d in self.graph.nodes(data=True) if d.get("plane") == "user"]

    def mark_node_covered(self, node_id: str, status: str = "covered"):
        if node_id in self.graph.nodes:
            self.graph.nodes[node_id]["coverage_status"] = status

    def get_coverage(self) -> dict:
        goals = [
            n for n, d in self.graph.nodes(data=True)
            if d.get("plane") == "origin" and d.get("type") == "goal"
        ]
        covered = [
            n for n in goals
            if self.graph.nodes[n].get("coverage_status") in ("partial", "covered")
        ]
        total = len(goals)
        return {
            "total": total,
            "covered": len(covered),
            "percentage": int(len(covered) / total * 100) if total else 0,
            "goal_ids": goals,
            "covered_ids": covered,
        }

    def get_deviations(self) -> list[dict]:
        deviations = []
        for n, d in self.graph.nodes(data=True):
            if d.get("plane") != "user":
                continue
            has_cross = any(
                data.get("plane") == "cross"
                for _, _, data in self.graph.out_edges(n, data=True)
            )
            if not has_cross:
                deviations.append(d)
        return deviations

    def find_relevant_nodes(self, query: str, top_k: int = 8) -> list[dict]:
        terms = [t.lower() for t in query.split() if len(t) > 3]
        if not terms:
            return list(self.get_origin_nodes())[:top_k]
        scored: list[tuple[int, dict]] = []
        for _, d in self.graph.nodes(data=True):
            text = f"{d.get('label', '')} {d.get('description', '')}".lower()
            score = sum(1 for t in terms if t in text)
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:top_k]]

    def to_cytoscape(self, plane: Optional[str] = None) -> dict:
        elements = []
        for node_id, d in self.graph.nodes(data=True):
            node_plane = d.get("plane", "origin")
            if plane and plane != "both" and node_plane != plane:
                continue
            elements.append({"data": {"id": node_id, **d}})

        for src, tgt, d in self.graph.edges(data=True):
            edge_plane = d.get("plane", "origin")
            if plane and plane != "both":
                src_plane = self.graph.nodes[src].get("plane")
                tgt_plane = self.graph.nodes[tgt].get("plane")
                if edge_plane == "cross":
                    # Only include cross edges if either endpoint is in the requested plane
                    if src_plane != plane and tgt_plane != plane:
                        continue
                elif edge_plane != plane:
                    continue
            edge_data = {"source": src, "target": tgt, **d}
            elements.append({"data": edge_data})

        return {"elements": elements}

    def to_json(self) -> dict:
        return {
            "nodes": [d for _, d in self.graph.nodes(data=True)],
            "edges": [d for _, _, d in self.graph.edges(data=True)],
        }

    @classmethod
    def from_json(cls, data: dict) -> "ProjectKG":
        kg = cls()
        for node in data.get("nodes", []):
            kg.graph.add_node(node["id"], **node)
        for edge in data.get("edges", []):
            kg.graph.add_edge(edge["source"], edge["target"], **edge)
        return kg
