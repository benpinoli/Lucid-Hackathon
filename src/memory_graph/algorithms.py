"""Pure sparse graph computations over immutable, revision-bound snapshots."""

from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol

from .domain import (
    Edge,
    Methodology,
    Node,
    NotFound,
    PathResult,
    PathStep,
    ResourceLimit,
    Revision,
    ValidationError,
    pair,
)


@dataclass(frozen=True)
class Snapshot:
    nodes: Mapping[str, Node]
    edges: Mapping[tuple[str, str], Edge]
    adjacency: Mapping[str, tuple[str, ...]]
    revision: Revision
    methodology: Methodology
    scope: str

    @classmethod
    def build(
        cls,
        nodes: Iterable[Node],
        edges: Iterable[Edge],
        revision: Revision,
        methodology: Methodology,
        scope: str,
    ) -> Snapshot:
        node_map = {}
        for node in nodes:
            if node.id in node_map:
                raise ValidationError("Snapshot contains duplicate node IDs")
            node_map[node.id] = node
        edge_map = {}
        adj = {key: [] for key in node_map}
        for edge in edges:
            key = pair(edge.source, edge.target)
            if key in edge_map:
                raise ValidationError("Snapshot contains a duplicate undirected edge")
            if key[0] not in adj or key[1] not in adj:
                raise ValidationError("Snapshot contains a dangling edge")
            edge_map[key] = edge
            adj[key[0]].append(key[1])
            adj[key[1]].append(key[0])
        return cls(
            MappingProxyType(node_map),
            MappingProxyType(edge_map),
            MappingProxyType({key: tuple(sorted(value)) for key, value in adj.items()}),
            revision,
            methodology,
            scope,
        )

    def neighbors(self, concept_id: str) -> tuple[str, ...]:
        if concept_id not in self.nodes:
            raise NotFound(f"Concept {concept_id} is outside this snapshot")
        return self.adjacency[concept_id]

    def edge_cost(self, a: str, b: str) -> float:
        edge = self.edges[pair(a, b)]
        ua, ub = self.nodes[a].understanding, self.nodes[b].understanding
        if not edge.in_human or ua is None or ub is None:
            raise ValidationError("Traversal requires a personal graph edge and understanding")
        cost = self.methodology.cost(edge.similarity, ua, ub, edge.familiarity)
        if not isfinite(cost) or cost <= 0:
            raise ValidationError("Dijkstra requires finite positive costs")
        return cost

    def components(self) -> tuple[tuple[str, ...], ...]:
        remaining = set(self.nodes)
        components = []
        for root in sorted(self.nodes):
            if root not in remaining:
                continue
            remaining.remove(root)
            queue, component = [root], []
            while queue:
                node = queue.pop()
                component.append(node)
                for neighbor in self.adjacency[node]:
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        queue.append(neighbor)
            components.append(tuple(sorted(component)))
        return tuple(components)

    def project(self, roots=None, **options):
        from .projections import project

        return project(self, roots, **options)

    def graphml(self) -> str:
        from .interchange import graphml

        return graphml(self)

    def sparse_matrix(self, *, semantics="similarity"):
        from .interchange import sparse_matrix

        return sparse_matrix(self, semantics=semantics)


class SnapshotReader(Protocol):
    def snapshot(self) -> Snapshot: ...


def shortest_path(
    snapshot: Snapshot, sources: Iterable[str], target: str, *, max_visited: int | None = None
) -> PathResult:
    if snapshot.scope != "human":
        raise ValidationError("Shortest paths operate on Human Brain edges only")
    sources = sorted(set(sources))
    if not sources:
        raise ValidationError("At least one source is required")
    if max_visited is not None and (type(max_visited) is not int or max_visited < 1):
        raise ValidationError("max_visited must be a positive integer")
    for node in [*sources, target]:
        snapshot.neighbors(node)
    distances = dict.fromkeys(sources, 0.0)
    previous: dict[str, str] = {}
    queue = [(0.0, source) for source in sources]
    heapq.heapify(queue)
    visited = 0
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        visited += 1
        if max_visited is not None and visited > max_visited:
            raise ResourceLimit("Path search exceeded max_visited; no optimal result returned")
        if node == target:
            path = [target]
            while path[-1] in previous:
                path.append(previous[path[-1]])
            path.reverse()
            steps = []
            for a, b in zip(path, path[1:]):
                edge, na, nb = snapshot.edges[pair(a, b)], snapshot.nodes[a], snapshot.nodes[b]
                steps.append(
                    PathStep(
                        a,
                        b,
                        edge.similarity,
                        na.understanding,
                        nb.understanding,
                        edge.familiarity,
                        snapshot.edge_cost(a, b),
                        edge.similarity_assessment_id,
                        na.assessment_id,
                        nb.assessment_id,
                        edge.familiarity_assessment_id,
                    )
                )
            return PathResult(
                "reachable",
                path[0],
                target,
                tuple(path),
                tuple(steps),
                distance,
                snapshot.revision,
                snapshot.methodology,
            )
        for neighbor in snapshot.adjacency[node]:
            candidate = distance + snapshot.edge_cost(node, neighbor)
            if not isfinite(candidate):
                raise ValidationError("Path cost overflow")
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))
    return PathResult(
        "unreachable", None, target, (), (), None, snapshot.revision, snapshot.methodology
    )


def neighborhood(
    snapshot: Snapshot,
    roots: Iterable[str] | None = None,
    *,
    hops: int = 1,
    max_nodes: int = 1000,
    max_edges: int = 5000,
) -> tuple[Snapshot, bool]:
    for name, value, minimum in (
        ("hops", hops, 0),
        ("max_nodes", max_nodes, 1),
        ("max_edges", max_edges, 0),
    ):
        if type(value) is not int or value < minimum:
            raise ValidationError(f"{name} must be an integer >= {minimum}")
    roots = sorted(snapshot.nodes) if roots is None else sorted(set(roots))
    for root in roots:
        snapshot.neighbors(root)
    selected = set(roots[:max_nodes])
    truncated = len(roots) > max_nodes
    queue = deque((root, 0) for root in sorted(selected))
    while queue:
        node, depth = queue.popleft()
        if depth >= hops:
            continue
        for neighbor in snapshot.adjacency[node]:
            if neighbor in selected:
                continue
            if len(selected) == max_nodes:
                truncated = True
                continue
            selected.add(neighbor)
            queue.append((neighbor, depth + 1))
    edges = []
    for key, edge in sorted(snapshot.edges.items()):
        if key[0] in selected and key[1] in selected:
            if len(edges) < max_edges:
                edges.append(edge)
            else:
                truncated = True
    return Snapshot.build(
        (snapshot.nodes[key] for key in sorted(selected)),
        edges,
        snapshot.revision,
        snapshot.methodology,
        snapshot.scope,
    ), truncated
