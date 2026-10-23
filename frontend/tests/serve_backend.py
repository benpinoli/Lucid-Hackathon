"""Isolated browser-test server. Public fixture token, ephemeral graph, loopback only."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / "src")]

import uvicorn
from memory_graph import MemoryGraph
from backend.app.main import create_app

graph = MemoryGraph.open("sqlite:///:memory:")
human = graph.create_human("00000000-0000-0000-0000-000000000064")
app = create_app(graph=graph, tokens={"browser-test-only-not-a-real-secret": human.human_id})
uvicorn.run(app, host="127.0.0.1", port=8001, ws_max_size=16384)
