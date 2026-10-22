import math
import random

import networkx as nx
import pytest
from hypothesis import given, settings, strategies as st

from memory_graph import (
    Methodology,
    NotFound,
    ResourceLimit,
    Snapshot,
    ValidationError,
    shortest_path,
)
from memory_graph.domain import Edge, Node, Revision
from conftest import cid


@pytest.mark.parametrize("bad", [True, False, None, "3", 0, 11, float("nan"), float("inf")])
def test_reject_invalid_scores(graph, bad):
    a, b = graph.god.add_concept("A"), graph.god.add_concept("B")
    human = graph.create_human()
    with pytest.raises(ValidationError):
        human.add_concept(a.id, understanding=bad)
    with pytest.raises(ValidationError):
        graph.god.connect(a.id, b.id, similarity=bad, provenance="test")
    graph.god.connect(a.id, b.id, similarity=5, provenance="test")
    human.add_concept(a.id, understanding=3)
    human.add_concept(b.id, understanding=3)
    with pytest.raises(ValidationError):
        human.connect(a.id, b.id, familiarity=bad)


@pytest.mark.parametrize("bad", [True, 0, -1, float("nan"), float("inf"), "small"])
def test_reject_invalid_epsilon(bad):
    with pytest.raises(ValidationError):
        Methodology(epsilon=bad)


@given(
    st.floats(1, 10, allow_nan=False),
    st.floats(1, 10, allow_nan=False),
    st.floats(1, 10, allow_nan=False),
)
def test_cost_properties(similarity, a, b):
    policy = Methodology()
    cost = policy.cost(similarity, a, b)
    assert math.isfinite(cost) and cost > 0
    assert cost == policy.cost(similarity, b, a)
    assert cost == 1 / (similarity * (a + b) + policy.epsilon)
    assert policy.cost(10, a, b) <= cost
    assert policy.cost(similarity, 10, b) <= cost
    assert policy.cost(similarity, a, 10) <= cost
    assert policy.cost(similarity, a, b, 1) == policy.cost(similarity, a, b, 10)


def test_manual_shortest_path_and_explanation(example):
    graph, human, (a, b, c, d, e) = example
    result = human.shortest_path(a, d)
    assert result.concepts == (a, b, d)
    assert result.total_cost == pytest.approx(1 / (8 * 14 + 1e-9) + 1 / (7 * 10 + 1e-9))
    assert sum(step.cost for step in result.steps) == result.total_cost
    assert all(step.similarity_assessment_id for step in result.steps)
    assert human.shortest_path(d, a).total_cost == result.total_cost
    assert human.shortest_path(a, e).status == "unreachable"
    assert human.shortest_path(a, e).total_cost is None
    assert human.shortest_path(a, a).concepts == (a,)
    assert human.shortest_path(a, a).total_cost == 0
    assert human.multi_source_shortest_path([c, b, b], d).source == b
    assert human.multi_source_shortest_path([a, d], d).total_cost == 0
    assert len(human.components()) == 2
    assert human.neighbors(a) == (b, c)
    assert graph.god.neighbors(a) == (b, c)
    assert len(graph.god.components()) == 2
    with pytest.raises(ValidationError):
        human.multi_source_shortest_path([], d)
    with pytest.raises(ResourceLimit):
        human.shortest_path(a, d, max_visited=1)
    with pytest.raises(NotFound):
        human.shortest_path(a, cid(999))
    with pytest.raises(ValidationError):
        shortest_path(graph.snapshot(), [a], d)


def test_deterministic_ties_independent_of_insertion_order():
    nodes = [Node(cid(i), str(i), "", 5, cid(100 + i), True) for i in range(1, 5)]
    edges = [
        Edge(cid(a), cid(b), 5, 5, cid(200 + a + b), cid(300 + a + b), True)
        for a, b in ((1, 3), (3, 4), (1, 2), (2, 4))
    ]
    for order in (edges, list(reversed(edges))):
        snapshot = Snapshot.build(
            nodes, order, Revision(cid(999), 1, 1, cid(1000)), Methodology(), "human"
        )
        assert shortest_path(snapshot, [cid(1)], cid(4)).concepts == (cid(1), cid(2), cid(4))
        assert shortest_path(snapshot, [cid(3), cid(2)], cid(4)).source == cid(2)


@settings(max_examples=35, deadline=None)
@given(st.integers(0, 100000), st.integers(2, 30))
def test_random_graphs_against_networkx(seed, count):
    rng = random.Random(seed)
    understanding = {cid(i): rng.uniform(1, 10) for i in range(1, count + 1)}
    nodes = [
        Node(key, key, "", value, cid(1000 + i), True)
        for i, (key, value) in enumerate(understanding.items())
    ]
    edges, oracle = [], nx.Graph()
    oracle.add_nodes_from(understanding)
    policy = Methodology()
    for a in range(1, count + 1):
        for b in range(a + 1, count + 1):
            if rng.random() < 0.15:
                similarity = rng.uniform(1, 10)
                edges.append(
                    Edge(cid(a), cid(b), similarity, 5, cid(2000 + a + b), cid(3000 + a + b), True)
                )
                oracle.add_edge(
                    cid(a),
                    cid(b),
                    weight=policy.cost(similarity, understanding[cid(a)], understanding[cid(b)]),
                )
    snapshot = Snapshot.build(nodes, edges, Revision(cid(999), 1, 1, cid(888)), policy, "human")
    sources = [cid(1), cid(count)]
    distances = nx.multi_source_dijkstra_path_length(oracle, sources)
    for target in understanding:
        result = shortest_path(snapshot, sources, target)
        if target not in distances:
            assert result.status == "unreachable"
        else:
            assert result.total_cost == pytest.approx(distances[target], rel=1e-12)
