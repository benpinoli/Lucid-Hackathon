import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import insert, update

from memory_graph import (
    AmbiguousConcept,
    Conflict,
    DependencyError,
    MemoryGraph,
    Methodology,
    MissingGodEdge,
    NotFound,
    ValidationError,
)
from memory_graph import schema_v1 as db
from memory_graph.domain import pair
from conftest import cid


def test_identity_symmetry_and_missing_references(graph):
    a = graph.god.add_concept("Same name")
    b = graph.god.add_concept("Same name")
    with pytest.raises(AmbiguousConcept):
        graph.god.get_concept("same NAME")
    graph.god.rename(a.id, "Renamed")
    graph.god.add_alias(a.id, "Old alias")
    assert graph.god.get_concept("old ALIAS").id == a.id
    assert graph.god.search("alias")[0].id == a.id
    graph.god.connect(b.id, a.id, similarity=6, provenance="test")
    with pytest.raises(Conflict):
        graph.god.connect(a.id, b.id, similarity=6, provenance="test")
    with pytest.raises(ValidationError):
        graph.god.connect(a.id, a.id, similarity=6, provenance="test")
    human = graph.create_human()
    with pytest.raises(NotFound):
        human.add_concept(cid(999), understanding=3)
    human.add_concept(a.id, understanding=4)
    with pytest.raises(NotFound):
        human.connect(a.id, b.id, familiarity=2)
    human.add_concept(b.id, understanding=7)
    human.connect(b.id, a.id, familiarity=2)
    with pytest.raises(Conflict):
        human.connect(a.id, b.id, familiarity=2)
    c = graph.god.add_concept("C")
    human.add_concept(c.id, understanding=2)
    with pytest.raises(MissingGodEdge):
        human.connect(a.id, c.id, familiarity=2)


def test_assessments_latest_accepted_history_and_retries(example):
    graph, human, (a, b, *_) = example
    old = human.shortest_path(a, b)
    old_snapshot = human.snapshot()
    human.assess_understanding(a, 2, assessed_at="2000-01-01T00:00:00+00:00")
    changed = human.shortest_path(a, b)
    assert changed.total_cost > old.total_cost
    assert old_snapshot.nodes[a].understanding == 8
    rejected = human.assess_understanding(a, 10, accepted=False)
    assert human.snapshot().nodes[a].understanding == 2
    revision = human.revision
    accepted = human.assess_understanding(a, 9, idempotency_key="assessment-1")
    assert human.assess_understanding(a, 9, idempotency_key="assessment-1") == accepted
    assert human.revision == revision + 1
    with pytest.raises(Conflict):
        human.assess_understanding(a, 8, idempotency_key="assessment-1")
    history = graph.history(kind="understanding", human_id=human.human_id, concept=a)
    assert [row["score"] for row in history] == [8, 2, 10, 9]
    assert history[2]["assessment_id"] == rejected and not history[2]["accepted"]
    baseline = human.shortest_path(a, b).total_cost
    human.assess_familiarity(b, a, 10)
    assert human.shortest_path(a, b).total_cost == baseline
    graph.god.assess_similarity(a, b, 10, provenance={"source": "revision"})
    assert human.shortest_path(a, b).total_cost < baseline
    assert len(graph.history(kind="similarity", concept=a, other=b)) == 2
    assert len(graph.history(kind="familiarity", human_id=human.human_id, concept=a, other=b)) == 2


def test_rollback_nested_savepoint_and_revision(example):
    graph, human, (a, b, *_) = example
    before = graph.export_json()
    with pytest.raises(RuntimeError):
        with graph.transaction():
            human.assess_understanding(a, 1)
            graph.god.rename(a, "Temporary")
            raise RuntimeError("force rollback")
    assert graph.export_json() == before
    with graph.transaction():
        with pytest.raises(ValidationError):
            # Insert edge then fail its provenance validation: savepoint must undo it.
            c = graph.god.add_concept("C new")
            graph.god.connect(a, c.id, similarity=5, provenance=None)
        graph.god.rename(b, "B kept")
    assert graph.god.get_concept(b).canonical_name == "B kept"
    assert c.id not in graph.god.neighbors(a)


def test_isolated_users_and_lifecycle(example):
    graph, human, (a, b, *_) = example
    other = graph.create_human()
    other.add_concept(a, understanding=1)
    with pytest.raises(NotFound):
        other.assess_understanding(b, 5)
    other.assess_understanding(a, 2)
    assert human.snapshot().nodes[a].understanding == 8
    with pytest.raises(DependencyError):
        graph.god.disconnect(a, b)
    graph.god.disconnect(a, b, cascade=True)
    assert b not in human.neighbors(a)
    with pytest.raises(NotFound):
        human.restore_edge(a, b)
    graph.god.restore_edge(a, b)
    human.restore_edge(a, b)
    assert b in human.neighbors(a)
    with pytest.raises(DependencyError):
        graph.god.delete_concept(a)
    graph.god.delete_concept(a, cascade=True)
    assert a not in human.snapshot().nodes
    with pytest.raises(NotFound):
        human.restore_concept(a)
    graph.god.restore_concept(a)
    human.restore_concept(a)
    assert human.snapshot().nodes[a].understanding == 8
    assert human.neighbors(a) == ()
    graph.delete_human(other.human_id)
    with pytest.raises(NotFound):
        other.snapshot()
    other = graph.restore_human(other.human_id)
    assert len(other.snapshot().nodes) == 0
    other.restore_concept(a)
    assert other.snapshot().nodes[a].understanding == 2


def test_sql_guards_and_audit_immutability(example):
    graph, human, (a, b, *_) = example
    with pytest.raises(Conflict):
        with graph.store.transaction(write=True) as connection:
            connection.execute(
                update(db.concepts).where(db.concepts.c.concept_id == a).values(active=False)
            )
    with pytest.raises(Conflict):
        with graph.store.transaction(write=True) as connection:
            connection.execute(
                update(db.human_concepts)
                .where(
                    db.human_concepts.c.human_id == human.human_id,
                    db.human_concepts.c.concept_id == a,
                )
                .values(score=1)
            )
    with pytest.raises(Conflict):
        with graph.store.transaction(write=True) as connection:
            connection.execute(update(db.ASSESSMENTS["understanding"]).values(score=1))
    with pytest.raises(Conflict):
        with graph.store.transaction(write=True) as connection:
            connection.execute(
                insert(db.human_concepts).values(
                    human_id=human.human_id,
                    concept_id=cid(999),
                    score=5,
                    active=True,
                    created_at="now",
                    updated_at="now",
                )
            )
    assert human.snapshot().nodes[a].understanding == 8
    assert pair(a, b) in human.snapshot().edges


def test_reopen_migration_and_configuration(tmp_path):
    url = "sqlite:///" + (tmp_path / "memory.db").as_posix()
    with MemoryGraph(url, methodology=Methodology(epsilon=0.01)) as graph:
        a = graph.god.add_concept("Persistent")
        human = graph.create_human()
        human.add_concept(a.id, understanding=3.5)
        human_id, before = human.human_id, graph.export_json()
    with MemoryGraph(url) as graph:
        assert graph.methodology.epsilon == 0.01
        assert graph.export_json() == before
        assert graph.human(human_id).snapshot().nodes[a.id].understanding == 3.5
        with graph.store.transaction() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "0002"
            )


def test_snapshot_isolation_and_optimistic_conflicts(tmp_path):
    url = "sqlite:///" + (tmp_path / "concurrent.db").as_posix()
    with MemoryGraph(url) as first, MemoryGraph(url) as second:
        a = first.god.add_concept("A")
        human = first.create_human()
        human.add_concept(a.id, understanding=2)
        other = second.human(human.human_id)
        expected = human.revision
        with first.store.transaction():
            original = human.snapshot()
            other.assess_understanding(a.id, 8, expected_revision=expected)
            assert human.snapshot().nodes[a.id].understanding == original.nodes[a.id].understanding
        assert human.snapshot().nodes[a.id].understanding == 8
        with pytest.raises(Conflict):
            human.assess_understanding(a.id, 4, expected_revision=expected)
        expected = human.revision

        def write(service, value):
            try:
                service.assess_understanding(a.id, value, expected_revision=expected)
                return "ok"
            except Conflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda args: write(*args), [(human, 3), (other, 7)]))
        assert sorted(results) == ["conflict", "ok"]


def test_import_roundtrip_dryrun_and_reject_corruption(example, tmp_path):
    graph, human, (a, b, *_) = example
    human.assess_understanding(a, 9, accepted=False)
    graph.god.disconnect(a, b, cascade=True)
    payload = json.loads(graph.export_json())
    url = "sqlite:///" + (tmp_path / "import.db").as_posix()
    with MemoryGraph(url) as target:
        report = target.import_json(payload, dry_run=True)
        assert report["valid"] and not target.god.search("A")
        target.import_json(payload)
        imported = json.loads(target.export_json())
        for name, rows in payload["tables"].items():
            if name != "archive_origins":
                assert imported["tables"][name] == rows
        assert imported["source"]["database_id"] != payload["source"]["database_id"]
        assert target.human(human.human_id).snapshot().nodes[a].understanding == 8
        with pytest.raises(Conflict):
            target.import_json(payload)
    with MemoryGraph(url) as target:
        assert target.human(human.human_id).snapshot().nodes[a].understanding == 8
    payload["tables"]["human_concepts"][0]["score"] = 10
    with MemoryGraph() as target:
        with pytest.raises(ValidationError):
            target.import_json(payload)
        assert target.god.search("A") == ()


def test_personal_archive_excludes_other_users(example):
    graph, human, (a, *_) = example
    other = graph.create_human()
    other.add_concept(a, understanding=2)
    payload = json.loads(graph.export_json(human_id=human.human_id))
    assert [row["human_id"] for row in payload["tables"]["humans"]] == [human.human_id]
    with MemoryGraph() as target:
        target.import_json(payload)
        assert target.human(human.human_id).snapshot().nodes[a].understanding == 8
        with pytest.raises(NotFound):
            target.human(other.human_id)
