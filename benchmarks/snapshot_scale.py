"""Algorithm-only scale test. Does not measure SQL, history or archive memory."""

import argparse
import json
import math
import platform
import time
from uuid import UUID

from memory_graph import Methodology, Snapshot, shortest_path
from memory_graph.domain import Edge, Node, Revision


def run(count=100_000):
    if count < 12 or count % 2:
        raise ValueError("Use an even node count >= 12")
    started = time.perf_counter()
    ids = [str(UUID(int=index + 1)) for index in range(count)]
    assessment_id = str(UUID(int=count + 1))
    nodes = [Node(key, key, "", 5, assessment_id, True) for key in ids]
    # Five ring offsets produce exactly 5*V unique undirected edges.
    edges = []
    for index, source in enumerate(ids):
        for offset in range(1, 6):
            a, b = sorted((source, ids[(index + offset) % count]))
            edges.append(Edge(a, b, 5, 5, assessment_id, assessment_id, True))
    snapshot = Snapshot.build(
        nodes, edges, Revision(str(UUID(int=0)), 1, 1, assessment_id), Methodology(), "human"
    )
    build_seconds = time.perf_counter() - started
    started = time.perf_counter()
    path = shortest_path(snapshot, [ids[0]], ids[count // 2])
    path_seconds = time.perf_counter() - started
    expected_hops = math.ceil((count // 2) / 5)
    assert path.status == "reachable" and len(path.steps) == expected_hops
    assert math.isclose(
        path.total_cost, expected_hops * snapshot.edge_cost(ids[0], ids[1]), rel_tol=1e-10
    )
    assert sum(len(neighbors) for neighbors in snapshot.adjacency.values()) == count * 10
    return {
        "scope": "algorithm_only",
        "nodes": count,
        "edges": len(snapshot.edges),
        "adjacency_entries": count * 10,
        "build_seconds": build_seconds,
        "path_seconds": path_seconds,
        "path_hops": len(path.steps),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "configuration_hash": snapshot.methodology.configuration_hash,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=100_000)
    args = parser.parse_args()
    print(json.dumps(run(args.nodes), indent=2))
