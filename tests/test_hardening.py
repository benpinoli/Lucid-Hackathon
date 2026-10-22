import json
from unittest.mock import patch

import pytest
from alembic import command
from sqlalchemy import select

from memory_graph import Conflict, MemoryGraph, Methodology, ResourceLimit, ValidationError
from memory_graph import schema_v1 as db
from memory_graph.domain import timestamp
from conftest import cid


def test_upgrade_previous_revision_preserves_data(tmp_path):
    url = "sqlite:///" + (tmp_path / "old.db").as_posix()
    original_upgrade = command.upgrade
    with patch(
        "memory_graph.storage.command.upgrade",
        side_effect=lambda config, _target: original_upgrade(config, "0001"),
    ):
        with MemoryGraph(url) as graph:
            a = graph.god.add_concept("Before upgrade")
            human = graph.create_human()
            human.add_concept(a.id, understanding=6)
            human_id = human.human_id
            archive = graph.export_json()
    with MemoryGraph(url) as graph:
        assert graph.export_json() == archive
        assert graph.human(human_id).snapshot().nodes[a.id].understanding == 6
        with graph.store.transaction() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "0002"
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM sqlite_master WHERE type='trigger'"
                ).scalar_one()
                > 0
            )


def test_direct_sql_accepted_pointer_cannot_reference_another_subject(example):
    graph, human, (a, b, *_) = example
    snapshot = human.snapshot()
    with pytest.raises(Conflict):
        with graph.store.transaction(write=True) as connection:
            connection.execute(
                db.human_concepts.update()
                .where(
                    db.human_concepts.c.human_id == human.human_id,
                    db.human_concepts.c.concept_id == a,
                )
                .values(
                    current_assessment_id=snapshot.nodes[b].assessment_id,
                    score=snapshot.nodes[b].understanding,
                )
            )
    assert human.snapshot().nodes[a].understanding == 8


@pytest.mark.parametrize(
    "corruption", ["dangling", "duplicate", "inactive", "methodology", "history", "uuid", "bool"]
)
def test_import_is_atomic_for_invalid_archives(example, corruption):
    graph, human, nodes = example
    payload = json.loads(graph.export_json())
    if corruption == "dangling":
        payload["tables"]["human_edges"][0]["b_id"] = cid(9000)
    elif corruption == "duplicate":
        payload["tables"]["concepts"].append(payload["tables"]["concepts"][0])
    elif corruption == "inactive":
        payload["tables"]["concepts"][0]["active"] = False
    elif corruption == "methodology":
        payload["methodology"]["epsilon"] = 0
    elif corruption == "history":
        payload["tables"]["assessment_decisions"].pop()
    elif corruption == "uuid":
        payload["tables"]["concepts"][0]["concept_id"] = "wrong"
    elif corruption == "bool":
        payload["tables"]["understanding_assessments"][0]["score"] = True
    with MemoryGraph() as target:
        before = target.export_json()
        with pytest.raises(ValidationError):
            target.import_json(payload)
        assert target.export_json() == before


def test_configuration_and_full_audit_roundtrip(example, tmp_path):
    graph, human, (a, b, *_) = example
    human.assess_understanding(a, 2, idempotency_key="retry")
    graph.god.delete_concept(a, cascade=True)
    graph.delete_human(human.human_id)
    payload = graph.export_json()
    with MemoryGraph() as target:
        target.import_json(payload)
        restored = target.restore_human(human.human_id)
        target.god.restore_concept(a)
        restored.restore_concept(a)
        assert restored.snapshot().nodes[a].understanding == 2
        history = target.history(kind="understanding", human_id=human.human_id, concept=a)
        assert len(history) == 2
    url = "sqlite:///" + (tmp_path / "configured.db").as_posix()
    with MemoryGraph(methodology=Methodology(epsilon=0.1)) as source:
        with MemoryGraph(url) as target:
            target.import_json(source.export_json())
            assert target.methodology.epsilon == 0.1
    with MemoryGraph(url) as target:
        assert target.methodology.epsilon == 0.1


def test_timestamp_validation_and_methodology_contract():
    assert timestamp("2020-01-01T01:00:00+01:00").startswith("2020-01-01T00:00:00")
    with pytest.raises(ValidationError):
        timestamp("2020-01-01T01:00:00")
    with pytest.raises(ValidationError):
        Methodology(formula_id="silently_changed")
    with pytest.raises(ValidationError):
        Methodology().cost(10**1000, 2, 2)


def test_import_cannot_escape_an_outer_transaction(graph):
    with MemoryGraph() as source:
        payload = source.export_json()
    with graph.transaction():
        with pytest.raises(Conflict):
            graph.import_json(payload)


def test_empty_graph_exports(graph):
    snapshot = graph.snapshot()
    assert snapshot.components() == ()
    assert snapshot.project().nodes == ()
    assert snapshot.sparse_matrix().matrix.shape == (0, 0)
    with graph.store.transaction() as connection:
        assert connection.execute(select(db.concepts)).first() is None


def test_snapshot_limits_fail_explicitly(example):
    graph, human, (a, b, *_) = example
    with pytest.raises(ResourceLimit):
        human.snapshot(max_nodes=2)
    with pytest.raises(ResourceLimit):
        graph.snapshot(max_edges=1)
    with pytest.raises(ResourceLimit):
        human.shortest_path(a, b, snapshot_max_edges=0)
    with pytest.raises(ValidationError):
        human.snapshot(max_nodes=True)
