"""Application services. All mutations are atomic and pass through the same invariants."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from functools import wraps
from importlib.resources import files
from typing import Callable, Iterable, ParamSpec, Self, TypeVar, cast

from sqlalchemy import and_, insert, or_, select, update

from . import schema_v1 as db
from .algorithms import Snapshot, neighborhood, shortest_path
from .domain import (
    AmbiguousConcept,
    Concept,
    Conflict,
    DependencyError,
    Edge,
    Methodology,
    MissingGodEdge,
    Node,
    NotFound,
    PathResult,
    ResourceLimit,
    Revision,
    ValidationError,
    canonical_json,
    finite,
    pair,
    score,
    text,
    timestamp,
    uid,
)
from .storage import Store


def _where(table, values):
    return and_(*(table.c[key] == value for key, value in values.items()))


def _one(connection, table, values, *, active=True):
    query = select(table).where(_where(table, values))
    if active and "active" in table.c:
        query = query.where(table.c.active.is_(True))
    row = connection.execute(query).mappings().first()
    if row is None:
        raise NotFound(f"No {'active ' if active else ''}{table.name}: {values}")
    return dict(row)


def _lifecycle():
    now = timestamp()
    return {"active": True, "created_at": now, "updated_at": now}


P = ParamSpec("P")
R = TypeVar("R")


def mutation(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self.graph.store.transaction(write=True) as connection:
            self.graph._check(connection, self.scope, kwargs.get("expected_revision"))
            return function(self, *args, **kwargs)

    return cast(Callable[P, R], wrapped)


class MemoryGraph:
    """Owns a store, configuration, and the universal graph service.

    Use an explicit context manager to close database connections. Each instance is
    thread-safe; independent instances coordinate through SQLite transactions.
    """

    def __init__(self, url: str = "sqlite:///:memory:", *, methodology: Methodology | None = None):
        self.store = Store(url)
        with self.store.transaction(write=True) as connection:
            state = connection.execute(select(db.state)).mappings().first()
            if methodology is None and state is not None:
                stored = connection.execute(
                    select(db.methodologies.c.configuration).where(
                        db.methodologies.c.configuration_hash == state["configuration_hash"]
                    )
                ).scalar_one()
                methodology = Methodology(**json.loads(stored))
            self._methodology = methodology or Methodology(
                **json.loads(files("memory_graph").joinpath("methodology.json").read_text())
            )
            existing = connection.execute(
                select(db.methodologies).where(
                    db.methodologies.c.configuration_hash == self.methodology.configuration_hash
                )
            ).first()
            if existing is None:
                connection.execute(
                    insert(db.methodologies).values(
                        configuration_hash=self.methodology.configuration_hash,
                        configuration=canonical_json(asdict(self.methodology)),
                    )
                )
            if state is None:
                connection.execute(
                    insert(db.state).values(
                        id=1,
                        database_id=uid(),
                        revision=0,
                        configuration_hash=self.methodology.configuration_hash,
                    )
                )
            else:
                connection.execute(
                    update(db.state)
                    .where(db.state.c.id == 1)
                    .values(configuration_hash=self.methodology.configuration_hash)
                )
        self.god = GodBrain(self)
        from .proposals import ProposalService

        self.proposals = ProposalService(self)

    @property
    def methodology(self) -> Methodology:
        return self._methodology

    @classmethod
    def open(
        cls, url: str = "sqlite:///:memory:", *, methodology: Methodology | None = None
    ) -> Self:
        return cls(url, methodology=methodology)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self) -> None:
        self.store.close()

    @contextmanager
    def transaction(self):
        """Group multiple public mutations into one all-or-nothing write."""
        with self.store.transaction(write=True):
            yield self

    def _check(self, connection, scope, expected):
        table, key = (db.state, {"id": 1}) if scope == "god" else (db.humans, {"human_id": scope})
        row = _one(connection, table, key)
        if expected is not None and (type(expected) is not int or row["revision"] != expected):
            raise Conflict(f"Expected revision {expected}, found {row['revision']}")

    def _event(self, connection, scope, operation, payload):
        table, key = (db.state, {"id": 1}) if scope == "god" else (db.humans, {"human_id": scope})
        connection.execute(
            update(table).where(_where(table, key)).values(revision=table.c.revision + 1)
        )
        revision = _one(connection, table, key, active=False)["revision"]
        connection.execute(
            insert(db.events).values(
                event_id=uid(),
                scope=scope,
                revision=revision,
                operation=operation,
                payload=canonical_json(payload),
                created_at=timestamp(),
            )
        )

    def _revision(self, connection, human_id=None):
        state = _one(connection, db.state, {"id": 1})
        human = _one(connection, db.humans, {"human_id": human_id}) if human_id else None
        return Revision(
            state["database_id"], state["revision"], human["revision"] if human else None, human_id
        )

    def create_human(self, human_id: str | None = None) -> HumanBrain:
        human_id = uid(human_id)
        with self.store.transaction(write=True) as connection:
            connection.execute(
                insert(db.humans).values(human_id=human_id, revision=0, **_lifecycle())
            )
            self._event(connection, human_id, "create_human", {"human_id": human_id})
        return HumanBrain(self, human_id)

    def human(self, human_id: str) -> HumanBrain:
        human_id = uid(human_id)
        with self.store.transaction() as connection:
            _one(connection, db.humans, {"human_id": human_id})
        return HumanBrain(self, human_id)

    def delete_human(self, human_id: str, *, expected_revision: int | None = None) -> None:
        human_id = uid(human_id)
        with self.store.transaction(write=True) as connection:
            self._check(connection, human_id, expected_revision)
            now = timestamp()
            for table in (db.human_edges, db.human_concepts, db.humans):
                connection.execute(
                    update(table)
                    .where(table.c.human_id == human_id)
                    .values(active=False, updated_at=now)
                )
            self._event(connection, human_id, "delete_human", {"human_id": human_id})

    def restore_human(self, human_id: str) -> HumanBrain:
        human_id = uid(human_id)
        with self.store.transaction(write=True) as connection:
            row = _one(connection, db.humans, {"human_id": human_id}, active=False)
            if row["active"]:
                raise Conflict("Human Brain is already active")
            connection.execute(
                update(db.humans)
                .where(db.humans.c.human_id == human_id)
                .values(active=True, updated_at=timestamp())
            )
            self._event(connection, human_id, "restore_human", {"human_id": human_id})
        return self.human(human_id)

    def _resolve(self, connection, value: str, *, active=True) -> str:
        try:
            concept_id = uid(value)
        except ValidationError:
            normalized = text(value).casefold()
            query = select(db.concepts.c.concept_id).where(
                or_(
                    db.concepts.c.normalized_name == normalized,
                    db.concepts.c.concept_id.in_(
                        select(db.aliases.c.concept_id).where(
                            db.aliases.c.normalized_alias == normalized
                        )
                    ),
                )
            )
            if active:
                query = query.where(db.concepts.c.active.is_(True))
            matches = list(connection.execute(query).scalars())
            if len(matches) > 1:
                raise AmbiguousConcept(f"Ambiguous concept name {value!r}; use a stable ID")
            if not matches:
                raise NotFound(f"Unknown concept {value!r}")
            return matches[0]
        _one(connection, db.concepts, {"concept_id": concept_id}, active=active)
        return concept_id

    def _assessment(
        self,
        connection,
        kind,
        subject,
        value,
        *,
        method,
        provenance=None,
        confidence=None,
        assessed_at=None,
        accepted=True,
        idempotency_key=None,
    ):
        value, method = score(value), text(method, "assessment method")
        if type(accepted) is not bool:
            raise ValidationError("accepted must be a boolean")
        if confidence is not None and not 0 <= finite(confidence, "confidence") <= 1:
            raise ValidationError("confidence must be between 0 and 1")
        if kind == "similarity" and provenance is None:
            raise ValidationError("Similarity requires explicit provenance")
        scope = subject.get("human_id", "god")
        request = canonical_json(
            dict(
                kind=kind,
                subject=subject,
                score=value,
                method=method,
                provenance=provenance,
                confidence=confidence,
                assessed_at=(timestamp(assessed_at) if assessed_at is not None else None),
                accepted=accepted,
            )
        )
        if idempotency_key is not None:
            idempotency_key = text(idempotency_key, "idempotency key")
            prior = (
                connection.execute(
                    select(db.retries).where(
                        db.retries.c.scope == scope, db.retries.c.key == idempotency_key
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                if prior["request"] != request:
                    raise Conflict("Idempotency key was previously used for a different request")
                return json.loads(prior["result"])
        assessment_id = uid()
        connection.execute(
            insert(db.ASSESSMENTS[kind]).values(
                **subject,
                assessment_id=assessment_id,
                score=value,
                assessed_at=timestamp(assessed_at),
                recorded_at=timestamp(),
                method=method,
                provenance=canonical_json(provenance),
                confidence=confidence,
            )
        )
        connection.execute(
            insert(db.decisions).values(
                decision_id=uid(),
                **{f"{kind}_id": assessment_id},
                accepted=accepted,
                decided_at=timestamp(),
            )
        )
        if accepted:
            table = db.SUBJECTS[kind][0]
            connection.execute(
                update(table)
                .where(_where(table, subject))
                .values(score=value, current_assessment_id=assessment_id, updated_at=timestamp())
            )
        self._event(
            connection,
            scope,
            "assess_" + kind,
            {"assessment_id": assessment_id, "accepted": accepted, **subject},
        )
        if idempotency_key is not None:
            connection.execute(
                insert(db.retries).values(
                    scope=scope,
                    key=idempotency_key,
                    request=request,
                    result=canonical_json(assessment_id),
                )
            )
        return assessment_id

    def snapshot(
        self, *, human_id=None, scope="god", max_nodes=100_000, max_edges=500_000
    ) -> Snapshot:
        if (
            type(max_nodes) is not int
            or max_nodes < 1
            or type(max_edges) is not int
            or max_edges < 0
        ):
            raise ValidationError("Snapshot limits require max_nodes >= 1 and max_edges >= 0")
        if scope not in ("god", "human", "comparison"):
            raise ValidationError("scope must be god, human, or comparison")
        if scope != "god" and human_id is None:
            raise ValidationError("Personal/comparison snapshots require human_id")
        if scope == "god" and human_id is not None:
            raise ValidationError("God Brain scope does not take a human_id")
        with self.store.transaction() as connection:
            revision = self._revision(connection, human_id)
            memberships, personal_edges = {}, {}
            if human_id:
                memberships = {
                    row["concept_id"]: row
                    for row in connection.execute(
                        select(db.human_concepts)
                        .where(
                            db.human_concepts.c.human_id == human_id,
                            db.human_concepts.c.active.is_(True),
                        )
                        .limit(max_nodes + 1)
                    ).mappings()
                }
                if len(memberships) > max_nodes:
                    raise ResourceLimit("Personal snapshot exceeds max_nodes")
                personal_edges = {
                    (row["a_id"], row["b_id"]): row
                    for row in connection.execute(
                        select(db.human_edges)
                        .where(
                            db.human_edges.c.human_id == human_id, db.human_edges.c.active.is_(True)
                        )
                        .limit(max_edges + 1)
                    ).mappings()
                }
                if len(personal_edges) > max_edges:
                    raise ResourceLimit("Personal snapshot exceeds max_edges")
            query = select(db.concepts).where(db.concepts.c.active.is_(True))
            if scope == "human":
                query = query.join(
                    db.human_concepts,
                    and_(
                        db.human_concepts.c.concept_id == db.concepts.c.concept_id,
                        db.human_concepts.c.human_id == human_id,
                        db.human_concepts.c.active.is_(True),
                    ),
                )
            nodes = []
            for row in connection.execute(query.limit(max_nodes + 1)).mappings():
                member = memberships.get(row["concept_id"])
                nodes.append(
                    Node(
                        row["concept_id"],
                        row["canonical_name"],
                        row["description"],
                        member["score"] if member else None,
                        member["current_assessment_id"] if member else None,
                        member is not None,
                    )
                )
            if len(nodes) > max_nodes:
                raise ResourceLimit("Snapshot exceeds max_nodes")
            edges = []
            # Human scope joins on its composite edge key, avoiding a global edge scan.
            query = select(db.god_edges).where(db.god_edges.c.active.is_(True))
            if scope == "human":
                query = query.join(
                    db.human_edges,
                    and_(
                        db.human_edges.c.a_id == db.god_edges.c.a_id,
                        db.human_edges.c.b_id == db.god_edges.c.b_id,
                        db.human_edges.c.human_id == human_id,
                        db.human_edges.c.active.is_(True),
                    ),
                )
            for row in connection.execute(query.limit(max_edges + 1)).mappings():
                personal = personal_edges.get((row["a_id"], row["b_id"]))
                edges.append(
                    Edge(
                        row["a_id"],
                        row["b_id"],
                        row["score"],
                        personal["score"] if personal else None,
                        row["current_assessment_id"],
                        personal["current_assessment_id"] if personal else None,
                        personal is not None,
                    )
                )
            if len(edges) > max_edges:
                raise ResourceLimit("Snapshot exceeds max_edges")
        return Snapshot.build(nodes, edges, revision, self.methodology, scope)

    def history(self, *, kind: str, human_id=None, concept=None, other=None) -> tuple[dict, ...]:
        if kind not in db.ASSESSMENTS:
            raise ValidationError("Unknown assessment kind")
        if kind != "similarity" and human_id is None:
            raise ValidationError("Personal history requires human_id")
        if kind == "similarity" and human_id is not None:
            raise ValidationError("Similarity history is universal")
        with self.store.transaction() as connection:
            table = db.ASSESSMENTS[kind]
            query = select(
                table, db.decisions.c.accepted, db.decisions.c.sequence, db.decisions.c.decided_at
            ).join(db.decisions, db.decisions.c[f"{kind}_id"] == table.c.assessment_id)
            if human_id:
                _one(connection, db.humans, {"human_id": uid(human_id)}, active=False)
                query = query.where(table.c.human_id == human_id)
            if concept:
                concept = self._resolve(connection, concept, active=False)
                if kind == "understanding":
                    query = query.where(table.c.concept_id == concept)
                elif other:
                    a, b = pair(concept, self._resolve(connection, other, active=False))
                    query = query.where(table.c.a_id == a, table.c.b_id == b)
                else:
                    query = query.where(or_(table.c.a_id == concept, table.c.b_id == concept))
            rows = []
            for row in connection.execute(query.order_by(db.decisions.c.sequence)).mappings():
                record = dict(row)
                record["provenance"] = json.loads(record["provenance"])
                rows.append(record)
            return tuple(rows)

    def export_json(self, *, human_id: str | None = None) -> str:
        from .interchange import export_json

        return export_json(self, human_id=human_id)

    def import_json(self, payload: str | dict, *, dry_run: bool = False) -> dict:
        from .interchange import import_json

        return import_json(self, payload, dry_run=dry_run)


class GodBrain:
    scope = "god"

    def __init__(self, graph: MemoryGraph):
        self.graph = graph

    @property
    def revision(self) -> int:
        with self.graph.store.transaction() as connection:
            return self.graph._revision(connection).god

    @mutation
    def add_concept(
        self,
        name: str,
        *,
        description: str = "",
        concept_id: str | None = None,
        expected_revision: int | None = None,
    ) -> Concept:
        connection = self.graph.store.connection
        name = text(name)
        if not isinstance(description, str):
            raise ValidationError("description must be text")
        values = dict(
            concept_id=uid(concept_id),
            canonical_name=name,
            normalized_name=name.casefold(),
            description=description,
            **_lifecycle(),
        )
        connection.execute(insert(db.concepts).values(**values))
        self.graph._event(connection, self.scope, "add_concept", values)
        return _concept(values)

    def get_concept(self, concept: str) -> Concept:
        with self.graph.store.transaction() as connection:
            concept_id = self.graph._resolve(connection, concept)
            return _concept(_one(connection, db.concepts, {"concept_id": concept_id}))

    def search(self, query: str, *, limit=50) -> tuple[Concept, ...]:
        query = text(query, "search query").casefold()
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValidationError("limit must be between 1 and 1000")
        with self.graph.store.transaction() as connection:
            matches = select(db.aliases.c.concept_id).where(
                db.aliases.c.normalized_alias.contains(query, autoescape=True)
            )
            rows = connection.execute(
                select(db.concepts)
                .where(
                    db.concepts.c.active.is_(True),
                    or_(
                        db.concepts.c.normalized_name.contains(query, autoescape=True),
                        db.concepts.c.concept_id.in_(matches),
                    ),
                )
                .order_by(db.concepts.c.normalized_name, db.concepts.c.concept_id)
                .limit(limit)
            ).mappings()
            return tuple(_concept(row) for row in rows)

    @mutation
    def rename(
        self,
        concept: str,
        name: str,
        *,
        description: str | None = None,
        expected_revision: int | None = None,
    ) -> None:
        connection = self.graph.store.connection
        concept_id, name = self.graph._resolve(connection, concept), text(name)
        values = dict(canonical_name=name, normalized_name=name.casefold(), updated_at=timestamp())
        if description is not None:
            if not isinstance(description, str):
                raise ValidationError("description must be text")
            values["description"] = description
        connection.execute(
            update(db.concepts).where(db.concepts.c.concept_id == concept_id).values(**values)
        )
        self.graph._event(connection, self.scope, "rename", {"concept_id": concept_id, **values})

    @mutation
    def add_alias(self, concept: str, alias: str, *, expected_revision: int | None = None) -> None:
        connection = self.graph.store.connection
        concept_id, alias = self.graph._resolve(connection, concept), text(alias, "alias")
        connection.execute(
            insert(db.aliases).values(
                concept_id=concept_id, alias=alias, normalized_alias=alias.casefold()
            )
        )
        self.graph._event(
            connection, self.scope, "add_alias", {"concept_id": concept_id, "alias": alias}
        )

    @mutation
    def connect(
        self,
        a: str,
        b: str,
        *,
        similarity: float,
        provenance: object,
        method: str = "direct",
        expected_revision: int | None = None,
    ) -> str:
        connection = self.graph.store.connection
        a, b = pair(self.graph._resolve(connection, a), self.graph._resolve(connection, b))
        subject = dict(a_id=a, b_id=b)
        connection.execute(
            insert(db.god_edges).values(**subject, score=score(similarity), **_lifecycle())
        )
        return self.graph._assessment(
            connection, "similarity", subject, similarity, method=method, provenance=provenance
        )

    @mutation
    def assess_similarity(
        self,
        a: str,
        b: str,
        similarity: float,
        *,
        provenance: object,
        method: str = "direct",
        confidence: float | None = None,
        assessed_at: str | datetime | None = None,
        accepted: bool = True,
        idempotency_key: str | None = None,
        expected_revision: int | None = None,
    ) -> str:
        connection = self.graph.store.connection
        a, b = pair(self.graph._resolve(connection, a), self.graph._resolve(connection, b))
        subject = dict(a_id=a, b_id=b)
        _one(connection, db.god_edges, subject)
        return self.graph._assessment(
            connection,
            "similarity",
            subject,
            similarity,
            method=method,
            provenance=provenance,
            confidence=confidence,
            assessed_at=assessed_at,
            accepted=accepted,
            idempotency_key=idempotency_key,
        )

    @mutation
    def disconnect(
        self, a: str, b: str, *, cascade: bool = False, expected_revision: int | None = None
    ) -> None:
        connection = self.graph.store.connection
        a, b = pair(self.graph._resolve(connection, a), self.graph._resolve(connection, b))
        condition = and_(db.human_edges.c.a_id == a, db.human_edges.c.b_id == b)
        affected = list(
            connection.execute(
                select(db.human_edges.c.human_id).where(
                    condition, db.human_edges.c.active.is_(True)
                )
            ).scalars()
        )
        _one(connection, db.god_edges, dict(a_id=a, b_id=b))
        if affected and not cascade:
            raise DependencyError("Active human edges depend on this relationship")
        now = timestamp()
        connection.execute(
            update(db.human_edges).where(condition).values(active=False, updated_at=now)
        )
        connection.execute(
            update(db.god_edges)
            .where(db.god_edges.c.a_id == a, db.god_edges.c.b_id == b)
            .values(active=False, updated_at=now)
        )
        for human_id in sorted(set(affected)):
            self.graph._event(connection, human_id, "cascade_disconnect", {"a": a, "b": b})
        self.graph._event(connection, self.scope, "disconnect", {"a": a, "b": b})

    @mutation
    def delete_concept(
        self, concept: str, *, cascade: bool = False, expected_revision: int | None = None
    ) -> None:
        connection = self.graph.store.connection
        concept_id = self.graph._resolve(connection, concept)
        condition = db.human_concepts.c.concept_id == concept_id
        affected = list(
            connection.execute(
                select(db.human_concepts.c.human_id).where(
                    condition, db.human_concepts.c.active.is_(True)
                )
            ).scalars()
        )
        if affected and not cascade:
            raise DependencyError("Active Human Brains contain this concept")
        now = timestamp()
        for table in (db.human_edges, db.god_edges):
            connection.execute(
                update(table)
                .where(or_(table.c.a_id == concept_id, table.c.b_id == concept_id))
                .values(active=False, updated_at=now)
            )
        connection.execute(
            update(db.human_concepts).where(condition).values(active=False, updated_at=now)
        )
        connection.execute(
            update(db.concepts)
            .where(db.concepts.c.concept_id == concept_id)
            .values(active=False, updated_at=now)
        )
        for human_id in sorted(set(affected)):
            self.graph._event(connection, human_id, "cascade_delete_concept", {"id": concept_id})
        self.graph._event(connection, self.scope, "delete_concept", {"id": concept_id})

    @mutation
    def restore_concept(self, concept: str, *, expected_revision: int | None = None) -> None:
        connection = self.graph.store.connection
        concept_id = self.graph._resolve(connection, concept, active=False)
        _restore(connection, db.concepts, {"concept_id": concept_id})
        self.graph._event(connection, self.scope, "restore_concept", {"id": concept_id})

    @mutation
    def restore_edge(self, a: str, b: str, *, expected_revision: int | None = None) -> None:
        connection = self.graph.store.connection
        a, b = pair(self.graph._resolve(connection, a), self.graph._resolve(connection, b))
        _restore(connection, db.god_edges, dict(a_id=a, b_id=b))
        self.graph._event(connection, self.scope, "restore_edge", {"a": a, "b": b})

    def snapshot(self, *, max_nodes: int = 100_000, max_edges: int = 500_000) -> Snapshot:
        return self.graph.snapshot(max_nodes=max_nodes, max_edges=max_edges)

    def neighbors(self, concept: str) -> tuple[str, ...]:
        with self.graph.store.transaction() as connection:
            concept_id = self.graph._resolve(connection, concept)
            forward = select(db.god_edges.c.b_id).where(
                db.god_edges.c.active.is_(True),
                db.god_edges.c.a_id == concept_id,
            )
            reverse = select(db.god_edges.c.a_id).where(
                db.god_edges.c.active.is_(True),
                db.god_edges.c.b_id == concept_id,
            )
            return tuple(sorted(connection.execute(forward.union_all(reverse)).scalars()))

    def components(self) -> tuple[tuple[str, ...], ...]:
        return self.snapshot().components()

    def subgraph(self, roots: Iterable[str] | None = None, **options) -> tuple[Snapshot, bool]:
        return neighborhood(self.snapshot(), roots, **options)


class HumanBrain:
    def __init__(self, graph: MemoryGraph, human_id: str):
        self.graph, self.human_id = graph, uid(human_id)
        self.scope = self.human_id

    @property
    def revision(self) -> int:
        with self.graph.store.transaction() as connection:
            return self.graph._revision(connection, self.human_id).human

    def _member(self, connection, concept):
        concept_id = self.graph._resolve(connection, concept)
        _one(connection, db.human_concepts, dict(human_id=self.human_id, concept_id=concept_id))
        return concept_id

    @mutation
    def add_concept(
        self,
        concept: str,
        *,
        understanding: float,
        method: str = "self_report",
        provenance: object = None,
        expected_revision: int | None = None,
    ) -> str:
        connection = self.graph.store.connection
        concept_id = self.graph._resolve(connection, concept)
        subject = dict(human_id=self.human_id, concept_id=concept_id)
        connection.execute(
            insert(db.human_concepts).values(**subject, score=score(understanding), **_lifecycle())
        )
        return self.graph._assessment(
            connection,
            "understanding",
            subject,
            understanding,
            method=method,
            provenance=provenance,
        )

    @mutation
    def connect(
        self,
        a: str,
        b: str,
        *,
        familiarity: float,
        method: str = "self_report",
        provenance: object = None,
        expected_revision: int | None = None,
    ) -> str:
        connection = self.graph.store.connection
        a, b = pair(self._member(connection, a), self._member(connection, b))
        try:
            _one(connection, db.god_edges, dict(a_id=a, b_id=b))
        except NotFound as exc:
            raise MissingGodEdge(
                "Create/validate the God Brain edge with explicit similarity first"
            ) from exc
        subject = dict(human_id=self.human_id, a_id=a, b_id=b)
        connection.execute(
            insert(db.human_edges).values(**subject, score=score(familiarity), **_lifecycle())
        )
        return self.graph._assessment(
            connection, "familiarity", subject, familiarity, method=method, provenance=provenance
        )

    @mutation
    def assess_understanding(
        self,
        concept: str,
        understanding: float,
        *,
        method: str = "self_report",
        provenance: object = None,
        confidence: float | None = None,
        assessed_at: str | datetime | None = None,
        accepted: bool = True,
        idempotency_key: str | None = None,
        expected_revision: int | None = None,
    ) -> str:
        connection = self.graph.store.connection
        concept_id = self._member(connection, concept)
        return self.graph._assessment(
            connection,
            "understanding",
            dict(human_id=self.human_id, concept_id=concept_id),
            understanding,
            method=method,
            provenance=provenance,
            confidence=confidence,
            assessed_at=assessed_at,
            accepted=accepted,
            idempotency_key=idempotency_key,
        )

    @mutation
    def assess_familiarity(
        self,
        a: str,
        b: str,
        familiarity: float,
        *,
        method: str = "self_report",
        provenance: object = None,
        confidence: float | None = None,
        assessed_at: str | datetime | None = None,
        accepted: bool = True,
        idempotency_key: str | None = None,
        expected_revision: int | None = None,
    ) -> str:
        connection = self.graph.store.connection
        a, b = pair(self._member(connection, a), self._member(connection, b))
        subject = dict(human_id=self.human_id, a_id=a, b_id=b)
        _one(connection, db.human_edges, subject)
        return self.graph._assessment(
            connection,
            "familiarity",
            subject,
            familiarity,
            method=method,
            provenance=provenance,
            confidence=confidence,
            assessed_at=assessed_at,
            accepted=accepted,
            idempotency_key=idempotency_key,
        )

    @mutation
    def disconnect(self, a: str, b: str, *, expected_revision: int | None = None) -> None:
        connection = self.graph.store.connection
        a, b = pair(self._member(connection, a), self._member(connection, b))
        subject = dict(human_id=self.human_id, a_id=a, b_id=b)
        _one(connection, db.human_edges, subject)
        connection.execute(
            update(db.human_edges)
            .where(_where(db.human_edges, subject))
            .values(active=False, updated_at=timestamp())
        )
        self.graph._event(connection, self.scope, "disconnect", subject)

    @mutation
    def delete_concept(self, concept: str, *, expected_revision: int | None = None) -> None:
        connection = self.graph.store.connection
        concept_id = self._member(connection, concept)
        connection.execute(
            update(db.human_edges)
            .where(
                db.human_edges.c.human_id == self.human_id,
                or_(db.human_edges.c.a_id == concept_id, db.human_edges.c.b_id == concept_id),
            )
            .values(active=False, updated_at=timestamp())
        )
        subject = dict(human_id=self.human_id, concept_id=concept_id)
        connection.execute(
            update(db.human_concepts)
            .where(_where(db.human_concepts, subject))
            .values(active=False, updated_at=timestamp())
        )
        self.graph._event(connection, self.scope, "delete_concept", subject)

    @mutation
    def restore_concept(self, concept: str, *, expected_revision: int | None = None) -> None:
        connection = self.graph.store.connection
        concept_id = self.graph._resolve(connection, concept)
        subject = dict(human_id=self.human_id, concept_id=concept_id)
        _restore(connection, db.human_concepts, subject)
        self.graph._event(connection, self.scope, "restore_concept", subject)

    @mutation
    def restore_edge(self, a: str, b: str, *, expected_revision: int | None = None) -> None:
        connection = self.graph.store.connection
        a, b = pair(self._member(connection, a), self._member(connection, b))
        _one(connection, db.god_edges, dict(a_id=a, b_id=b))
        subject = dict(human_id=self.human_id, a_id=a, b_id=b)
        _restore(connection, db.human_edges, subject)
        self.graph._event(connection, self.scope, "restore_edge", subject)

    def snapshot(self, *, max_nodes: int = 100_000, max_edges: int = 500_000) -> Snapshot:
        return self.graph.snapshot(
            human_id=self.human_id, scope="human", max_nodes=max_nodes, max_edges=max_edges
        )

    def shortest_path(self, source: str, target: str, **options) -> PathResult:
        return self.multi_source_shortest_path([source], target, **options)

    def multi_source_shortest_path(
        self,
        sources: Iterable[str],
        target: str,
        *,
        snapshot_max_nodes=100_000,
        snapshot_max_edges=500_000,
        **options,
    ) -> PathResult:
        # Resolve names and load the snapshot under the same read transaction.
        with self.graph.store.transaction() as connection:
            resolved = [self._member(connection, source) for source in sources]
            target = self._member(connection, target)
            snapshot = self.snapshot(max_nodes=snapshot_max_nodes, max_edges=snapshot_max_edges)
        return shortest_path(snapshot, resolved, target, **options)

    def neighbors(self, concept: str) -> tuple[str, ...]:
        with self.graph.store.transaction() as connection:
            _one(connection, db.humans, {"human_id": self.human_id})
            concept_id = self._member(connection, concept)
            forward = select(db.human_edges.c.b_id).where(
                db.human_edges.c.human_id == self.human_id,
                db.human_edges.c.active.is_(True),
                db.human_edges.c.a_id == concept_id,
            )
            reverse = select(db.human_edges.c.a_id).where(
                db.human_edges.c.human_id == self.human_id,
                db.human_edges.c.active.is_(True),
                db.human_edges.c.b_id == concept_id,
            )
            return tuple(sorted(connection.execute(forward.union_all(reverse)).scalars()))

    def components(self) -> tuple[tuple[str, ...], ...]:
        return self.snapshot().components()

    def subgraph(self, roots: Iterable[str] | None = None, **options) -> tuple[Snapshot, bool]:
        return neighborhood(self.snapshot(), roots, **options)


def _concept(row):
    return Concept(
        row["concept_id"],
        row["canonical_name"],
        row["description"],
        row["created_at"],
        row["updated_at"],
    )


def _restore(connection, table, subject):
    row = _one(connection, table, subject, active=False)
    if row["active"]:
        raise Conflict("Record is already active")
    connection.execute(
        update(table).where(_where(table, subject)).values(active=True, updated_at=timestamp())
    )
