"""Run with: python examples/basic.py (after editable installation)."""

import json

from memory_graph import MemoryGraph


def main():
    with MemoryGraph() as graph:
        with graph.transaction():
            algebra = graph.god.add_concept("Linear Algebra")
            quantum = graph.god.add_concept("Quantum Mechanics")
            graph.god.connect(
                algebra.id,
                quantum.id,
                similarity=8,
                provenance={"source": "demonstration", "reviewed": True},
            )
            human = graph.create_human()
            human.add_concept(algebra.id, understanding=9)
            human.add_concept(quantum.id, understanding=3)
            human.connect(algebra.id, quantum.id, familiarity=5)
        path = human.shortest_path(algebra.id, quantum.id)
        projection = human.snapshot().project([algebra.id], hops=1)
        print(
            json.dumps(
                {
                    "path": path.to_dict(),
                    "graph": projection.to_dict(),
                    "overlay": projection.path_overlay(path),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
