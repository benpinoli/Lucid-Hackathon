"""Versioned node/link contracts for future force-directed graph viewers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .algorithms import Snapshot, neighborhood
from .domain import (
    Conflict,
    PathResult,
    Revision,
    ValidationError,
    finite,
    pair,
)


def edge_id(snapshot: Snapshot, a: str, b: str) -> str:
    a, b = pair(a, b)
    scope = snapshot.revision.human_id if snapshot.scope == "human" else "god"
    return f"{scope}:{a}:{b}"


@dataclass(frozen=True)
class GraphProjection:
    schema_version: int
    scope: str
    revision: Revision
    configuration_hash: str
    nodes: tuple[dict, ...]
    links: tuple[dict, ...]
    filters: dict
    limits: dict
    counts: dict
    truncated: bool

    def to_dict(self) -> dict:
        return asdict(self)

    def path_overlay(self, path: PathResult) -> dict:
        if path.revision != self.revision or (
            path.methodology.configuration_hash != self.configuration_hash
        ):
            raise Conflict("Path and projection revisions/configuration differ; refresh both")
        node_ids = {node["id"] for node in self.nodes}
        links = {pair(link["source"], link["target"]): link["id"] for link in self.links}
        if not set(path.concepts) <= node_ids:
            raise Conflict("Path is not fully visible; expand the projection")
        if any(pair(step.source, step.target) not in links for step in path.steps):
            raise Conflict("Path links are not fully visible; expand the projection")
        return {
            "schema_version": 1,
            "revision": asdict(self.revision),
            "node_ids": list(path.concepts),
            "link_ids": [links[pair(step.source, step.target)] for step in path.steps],
            "explanation": path.to_dict(),
        }


def project(
    snapshot: Snapshot,
    roots: Iterable[str] | None = None,
    *,
    hops=1,
    max_nodes=1000,
    max_edges=5000,
    expected_revision: Revision | None = None,
    membership="all",
    min_similarity=None,
    min_understanding=None,
    min_familiarity=None,
    max_cost=None,
) -> GraphProjection:
    """Filter, then take a deterministic bounded neighborhood.

    Degrees describe the filtered snapshot before neighborhood/edge truncation.
    Null personal scores do not pass personal-score filters.
    This bounds output, not the memory needed to load the source snapshot.
    """
    if expected_revision is not None and snapshot.revision != expected_revision:
        raise Conflict("Stale expansion revision; refresh the graph")
    if membership not in ("all", "human", "unrepresented"):
        raise ValidationError("Unknown membership filter")
    if snapshot.scope == "god" and (
        membership != "all"
        or min_understanding is not None
        or min_familiarity is not None
        or max_cost is not None
    ):
        raise ValidationError("Personal filters require human or comparison scope")
    filters = dict(
        membership=membership,
        min_similarity=min_similarity,
        min_understanding=min_understanding,
        min_familiarity=min_familiarity,
        max_cost=max_cost,
    )
    for name, value in filters.items():
        if name != "membership" and value is not None:
            value = finite(value, name)
            if name == "max_cost":
                if value <= 0:
                    raise ValidationError("max_cost must be positive")
            elif not 1 <= value <= 10:
                raise ValidationError(f"{name} must be in [1, 10]")
    nodes = {}
    for key, node in snapshot.nodes.items():
        if membership == "human" and not node.in_human:
            continue
        if membership == "unrepresented" and node.in_human:
            continue
        if min_understanding is not None and (
            node.understanding is None or node.understanding < min_understanding
        ):
            continue
        nodes[key] = node
    edges = []
    for key, edge in snapshot.edges.items():
        if key[0] not in nodes or key[1] not in nodes:
            continue
        if min_similarity is not None and edge.similarity < min_similarity:
            continue
        if min_familiarity is not None and (
            edge.familiarity is None or edge.familiarity < min_familiarity
        ):
            continue
        if max_cost is not None and (not edge.in_human or snapshot.edge_cost(*key) > max_cost):
            continue
        edges.append(edge)
    filtered = Snapshot.build(
        nodes.values(), edges, snapshot.revision, snapshot.methodology, snapshot.scope
    )
    bounded, truncated = neighborhood(
        filtered, roots, hops=hops, max_nodes=max_nodes, max_edges=max_edges
    )
    return GraphProjection(
        1,
        snapshot.scope,
        snapshot.revision,
        snapshot.methodology.configuration_hash,
        tuple(
            {
                **asdict(node),
                "degree": len(filtered.adjacency[key]),
                "degree_scope": "filtered_snapshot",
            }
            for key, node in sorted(bounded.nodes.items())
        ),
        tuple(
            {
                "id": edge_id(snapshot, *key),
                **asdict(edge),
                "traversal_cost": snapshot.edge_cost(*key) if edge.in_human else None,
            }
            for key, edge in sorted(bounded.edges.items())
        ),
        filters,
        dict(hops=hops, max_nodes=max_nodes, max_edges=max_edges),
        dict(
            filtered_nodes=len(filtered.nodes),
            filtered_links=len(filtered.edges),
            returned_nodes=len(bounded.nodes),
            returned_links=len(bounded.edges),
        ),
        truncated,
    )
