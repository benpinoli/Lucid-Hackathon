"""Measured in-process backend timings; does not measure network, GPU or headset."""
import json
import platform
import statistics
from time import perf_counter

from memory_graph import MemoryGraph

from backend.app.service import TutorService


def percentiles(samples):
    ordered = sorted(samples)
    return {"p50_ms": statistics.median(ordered),
            "p95_ms": ordered[int((len(ordered) - 1) * .95)],
            "p99_ms": ordered[int((len(ordered) - 1) * .99)]}


def run():
    with MemoryGraph.open() as graph:
        human = graph.create_human()
        service = TutorService(graph)
        lesson = service.create(human.human_id)["lesson_id"]
        samples = []
        for epoch in range(1000):
            start = perf_counter()
            service.turn(human.human_id, lesson, "Explain dot product", epoch)
            samples.append((perf_counter() - start) * 1000)
        return {"conditions": "1000 in-process deterministic turns; 6 nodes; no provider/network",
                "python": platform.python_version(), "platform": platform.platform(),
                "turn_plan_validate_commit": percentiles(samples),
                "headset_frame_timing": "unmeasured", "graph_10k_50k": "unmeasured"}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
