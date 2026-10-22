from io import StringIO

import networkx as nx
import pytest

from memory_graph import Conflict, NotFound, ValidationError
from memory_graph.domain import pair
from conftest import cid


def test_projection_bounds_filters_and_overlay(example):
    graph, human, (a, b, c, d, e) = example
    snapshot = human.snapshot()
    projection = snapshot.project([a], hops=1)
    assert {node["id"] for node in projection.nodes} == {a, b, c}
    assert len(projection.links) == 2 and not projection.truncated
    assert all(
        link["source"] in {a, b, c} and link["target"] in {a, b, c} for link in projection.links
    )
    truncated = snapshot.project([a], hops=5, max_nodes=2, max_edges=0)
    assert truncated.truncated and len(truncated.nodes) == 2 and not truncated.links
    path = human.shortest_path(a, d)
    full = snapshot.project()
    assert full.path_overlay(path)["node_ids"] == list(path.concepts)
    with pytest.raises(Conflict):
        projection.path_overlay(path)
    filtered = snapshot.project(min_understanding=5, min_similarity=5)
    assert {node["id"] for node in filtered.nodes} == {a, b}
    assert len(filtered.links) == 1
    human.assess_understanding(a, 9)
    with pytest.raises(Conflict):
        human.snapshot().project(expected_revision=snapshot.revision)
    with pytest.raises(Conflict):
        human.snapshot().project().path_overlay(path)
    with pytest.raises(NotFound):
        snapshot.project([cid(9000)])
    with pytest.raises(ValidationError):
        snapshot.project(max_nodes=0)
    with pytest.raises(ValidationError):
        snapshot.project(min_similarity=float("nan"))


def test_comparison_projection_preserves_unknowns(example):
    graph, human, (a, b, *_) = example
    unknown = graph.god.add_concept("Unknown")
    graph.god.connect(a, unknown.id, similarity=4, provenance="test")
    comparison = graph.snapshot(human_id=human.human_id, scope="comparison")
    projection = comparison.project()
    node = next(node for node in projection.nodes if node["id"] == unknown.id)
    assert node["understanding"] is None and not node["in_human"]
    link = next(link for link in projection.links if unknown.id in (link["source"], link["target"]))
    assert link["traversal_cost"] is None and link["familiarity"] is None
    assert comparison.project(membership="unrepresented").nodes[0]["id"] == unknown.id
    assert pair(a, b) in {
        pair(link["source"], link["target"]) for link in comparison.project(min_familiarity=1).links
    }
    assert unknown.id not in {node["id"] for node in comparison.project(min_understanding=1).nodes}
    with pytest.raises(ValidationError):
        graph.snapshot().project(min_understanding=1)


def test_graphml_and_sparse_export(example):
    graph, human, (a, b, *_) = example
    snapshot = human.snapshot()
    parsed = nx.read_graphml(StringIO(snapshot.graphml()))
    assert not parsed.is_directed()
    assert len(parsed.nodes) == 5 and len(parsed.edges) == 4
    assert parsed.edges[a, b]["similarity"] == 8
    for semantics in ("similarity", "familiarity", "cost"):
        exported = snapshot.sparse_matrix(semantics=semantics)
        matrix = exported.matrix
        i, j = exported.concept_ids.index(a), exported.concept_ids.index(b)
        expected = (
            8
            if semantics == "similarity"
            else 5
            if semantics == "familiarity"
            else snapshot.edge_cost(a, b)
        )
        assert matrix[i, j] == expected and matrix[j, i] == expected
        assert matrix.nnz == 8 and (matrix.diagonal() == 0).all()
    with pytest.raises(ValidationError):
        graph.snapshot().sparse_matrix(semantics="cost")


def proposal(graph, commands, humans=()):
    return {
        "source": "test-provider",
        "evidence": ["coursework"],
        "confidence": 0.6,
        "expected_revisions": {
            "god": graph.god.revision,
            **{human.human_id: human.revision for human in humans},
        },
        "commands": commands,
    }


def test_proposal_acceptance_atomicity_and_retry(graph):
    human = graph.create_human()
    concept_id = cid(300)
    payload = proposal(
        graph,
        [
            {
                "operation": "add_concept",
                "arguments": {"concept_id": concept_id, "name": "Proposed"},
            },
            {
                "operation": "add_human_concept",
                "arguments": {
                    "human_id": human.human_id,
                    "concept": concept_id,
                    "understanding": 4,
                },
            },
        ],
        [human],
    )
    proposal_id = graph.proposals.submit(payload)
    assert not graph.god.search("Proposed")
    accepted = graph.proposals.accept(proposal_id)
    assert graph.proposals.accept(proposal_id) == accepted
    assert human.snapshot().nodes[concept_id].understanding == 4
    assert graph.proposals.get(proposal_id)["payload"]["confidence"] == 0.6
    assert graph.proposals.get(proposal_id)["status"] == "accepted"

    failing = proposal(
        graph,
        [
            {"operation": "add_concept", "arguments": {"concept_id": cid(301), "name": "Rollback"}},
            {
                "operation": "connect_god",
                "arguments": {"a": cid(301), "b": cid(9999), "similarity": 5, "provenance": "test"},
            },
        ],
    )
    failing_id = graph.proposals.submit(failing)
    before = graph.god.revision
    with pytest.raises(NotFound):
        graph.proposals.accept(failing_id)
    assert graph.god.revision == before and not graph.god.search("Rollback")
    assert graph.proposals.get(failing_id)["status"] == "pending"
    graph.proposals.reject(failing_id, reason="Invalid endpoint")
    with pytest.raises(Conflict):
        graph.proposals.accept(failing_id)


def test_stale_proposal_and_untrusted_commands(graph):
    payload = proposal(
        graph,
        [{"operation": "add_concept", "arguments": {"concept_id": cid(301), "name": "Stale"}}],
    )
    proposal_id = graph.proposals.submit(payload)
    graph.god.add_concept("Unrelated edit")
    with pytest.raises(Conflict):
        graph.proposals.accept(proposal_id)
    payload["commands"][0]["operation"] = "delete_human"
    with pytest.raises(ValidationError):
        graph.proposals.submit(payload)
