import json
import inspect
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from memory_graph import Methodology
from memory_graph import GodBrain, HumanBrain


def test_documented_and_packaged_methodology_match():
    root = json.loads((Path(__file__).parents[1] / "methodology.json").read_text())
    packaged = json.loads(files("memory_graph").joinpath("methodology.json").read_text())
    assert root == packaged == json.loads(json.dumps(asdict(Methodology())))


def test_public_mutations_expose_domain_arguments():
    for service in (GodBrain, HumanBrain):
        for name in ("add_concept", "connect", "disconnect", "delete_concept", "restore_concept"):
            parameters = inspect.signature(getattr(service, name)).parameters
            assert "connection" not in parameters
            assert "expected_revision" in parameters
