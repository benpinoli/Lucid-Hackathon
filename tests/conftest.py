from uuid import UUID

import pytest

from memory_graph import MemoryGraph


def cid(number):
    return str(UUID(int=number))


@pytest.fixture
def graph():
    with MemoryGraph() as graph:
        yield graph


@pytest.fixture
def example(graph):
    """A diamond with a disconnected node and independently scored edges."""
    with graph.transaction():
        nodes = [
            graph.god.add_concept(name, concept_id=cid(index + 1)).id
            for index, name in enumerate("ABCDE")
        ]
        human = graph.create_human(cid(100))
        for node, understanding in zip(nodes, (8, 6, 2, 4, 1)):
            human.add_concept(node, understanding=understanding)
        for a, b, similarity in ((0, 1, 8), (1, 3, 7), (0, 2, 2), (2, 3, 3)):
            graph.god.connect(
                nodes[a], nodes[b], similarity=similarity, provenance={"source": "fixture"}
            )
            human.connect(nodes[a], nodes[b], familiarity=5)
    return graph, human, nodes
