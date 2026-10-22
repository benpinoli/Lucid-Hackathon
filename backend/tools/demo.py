"""Create a separate deterministic demonstration database, never the memory agent's DB."""
import json
import secrets
from pathlib import Path
from uuid import UUID

from memory_graph import MemoryGraph


def seed(path="backend/demo.db"):
    if Path(path).exists():
        # Existing DB is reopened but never reseeded or modified by this command.
        return str(UUID(int=100))
    with MemoryGraph.open(f"sqlite:///{path}") as graph:
        with graph.transaction():
            a = graph.god.add_concept("Vectors", concept_id=str(UUID(int=1))).id
            b = graph.god.add_concept("Dot product", concept_id=str(UUID(int=2))).id
            graph.god.connect(a, b, similarity=8, provenance={"source": "authored_demo_v1"})
            human = graph.create_human(str(UUID(int=100)))
            human.add_concept(a, understanding=9)
            human.add_concept(b, understanding=3)
            human.connect(a, b, familiarity=5)
    return human.human_id


if __name__ == "__main__":
    user = seed()
    print("$env:LUCID_DATABASE_URL='sqlite:///backend/demo.db'")
    print("$env:LUCID_TOKENS_JSON='" + json.dumps({secrets.token_urlsafe(32): user}) + "'")
    print("# Copy the two assignments into your terminal; keep this credential private.")
