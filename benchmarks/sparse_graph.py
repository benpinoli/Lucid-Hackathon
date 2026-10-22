"""Reproducible sparse graph benchmark through public application APIs.

python benchmarks/sparse_graph.py --nodes 1000 --edges 5000 --shape uniform
python benchmarks/sparse_graph.py --nodes 1000 --edges 5000 --shape hub
python benchmarks/sparse_graph.py --nodes 1000 --edges 5000 --shape disconnected
"""

import argparse
import json
import platform
import random
import tempfile
import time
import tracemalloc
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

from sqlalchemy import event

from memory_graph import MemoryGraph


def run(nodes, edges, seed=42, shape="uniform", trace_memory=False):
    if nodes < 2 or edges < 0 or shape not in ("uniform", "hub", "disconnected"):
        raise ValueError("Invalid graph parameters")
    eligible = nodes if shape != "disconnected" else nodes // 2
    maximum = eligible * (eligible - 1) // 2
    if edges > maximum:
        raise ValueError(f"At most {maximum} edges for this topology")
    rng = random.Random(seed)
    pairs = set()
    if shape == "hub":
        pairs.update((0, index) for index in range(1, min(nodes, edges + 1)))
    while len(pairs) < edges:
        a, b = sorted(rng.sample(range(eligible), 2))
        pairs.add((a, b))
    ids = [str(UUID(int=index + 1)) for index in range(nodes)]
    with tempfile.TemporaryDirectory(prefix="memory_graph_benchmark_") as directory:
        database = Path(directory) / "graph.db"
        with MemoryGraph("sqlite:///" + database.as_posix()) as graph:
            queries = [0]

            @event.listens_for(graph.store.engine, "before_cursor_execute")
            def query_count(*_args):
                queries[0] += 1

            if trace_memory:
                tracemalloc.start()
            started = time.perf_counter()
            with graph.transaction():
                for index, concept_id in enumerate(ids):
                    graph.god.add_concept(f"Concept {index}", concept_id=concept_id)
                human = graph.create_human()
                for concept_id in ids:
                    human.add_concept(concept_id, understanding=rng.uniform(1, 10))
                for a, b in sorted(pairs):
                    graph.god.connect(
                        ids[a],
                        ids[b],
                        similarity=rng.uniform(1, 10),
                        provenance={"source": "seeded_benchmark", "seed": seed},
                    )
                    human.connect(ids[a], ids[b], familiarity=rng.uniform(1, 10))
            write_seconds = time.perf_counter() - started
            started, before_queries = time.perf_counter(), queries[0]
            snapshot = human.snapshot()
            snapshot_seconds, snapshot_queries = (
                time.perf_counter() - started,
                queries[0] - before_queries,
            )
            started, before_queries = time.perf_counter(), queries[0]
            human.neighbors(ids[0])
            neighbor_seconds, neighbor_queries = (
                time.perf_counter() - started,
                queries[0] - before_queries,
            )
            started = time.perf_counter()
            from memory_graph.algorithms import shortest_path

            path = shortest_path(snapshot, [ids[0]], ids[-1])
            path_seconds = time.perf_counter() - started
            started = time.perf_counter()
            projection = snapshot.project([ids[0]], hops=2, max_nodes=500, max_edges=2000)
            projection_seconds = time.perf_counter() - started
            started = time.perf_counter()
            archive = graph.export_json()
            export_seconds = time.perf_counter() - started
            peak_memory = tracemalloc.get_traced_memory()[1] if trace_memory else None
            if trace_memory:
                tracemalloc.stop()
            config_hash = graph.methodology.configuration_hash
        return {
            "nodes": nodes,
            "edges": edges,
            "seed": seed,
            "shape": shape,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "dependencies": {name: version(name) for name in ("SQLAlchemy", "alembic")},
            "configuration_hash": config_hash,
            "write_seconds": write_seconds,
            "snapshot_seconds": snapshot_seconds,
            "snapshot_queries": snapshot_queries,
            "neighbor_seconds": neighbor_seconds,
            "neighbor_queries": neighbor_queries,
            "path_seconds": path_seconds,
            "path_status": path.status,
            "projection_seconds": projection_seconds,
            "projection_truncated": projection.truncated,
            "export_seconds": export_seconds,
            "archive_bytes": len(archive.encode()),
            "database_bytes": database.stat().st_size,
            "peak_traced_bytes": peak_memory,
            "trace_memory": trace_memory,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=1000)
    parser.add_argument("--edges", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--shape", choices=["uniform", "hub", "disconnected"], default="uniform")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--trace-memory",
        action="store_true",
        help="Track Python allocations; this adds substantial timing overhead",
    )
    arguments = parser.parse_args()
    result = json.dumps(
        run(
            arguments.nodes,
            arguments.edges,
            arguments.seed,
            arguments.shape,
            arguments.trace_memory,
        ),
        indent=2,
    )
    if arguments.output:
        arguments.output.write_text(result + "\n", encoding="utf-8")
    print(result)
