"""Lossless JSON archives, GraphML projections and optional sparse matrix exports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from xml.etree import ElementTree as ET

from sqlalchemy import insert, select, update

from . import schema_v1 as db
from .domain import (
    Conflict,
    Methodology,
    Revision,
    ValidationError,
    canonical_json,
    score,
    timestamp,
    uid,
)

ARCHIVE_TABLES = [
    db.concepts,
    db.aliases,
    db.humans,
    db.god_edges,
    db.human_concepts,
    db.human_edges,
    *db.ASSESSMENTS.values(),
    db.decisions,
    db.events,
    db.methodologies,
    db.retries,
    db.proposals,
    db.origins,
]


def export_json(graph, *, human_id=None) -> str:
    with graph.store.transaction() as connection:
        state = dict(connection.execute(select(db.state)).mappings().one())
        state["configuration_hash"] = graph.methodology.configuration_hash
        tables = {
            table.name: [
                dict(row)
                for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for table in ARCHIVE_TABLES
        }
        if human_id is not None:
            human_id = uid(human_id)
            if not any(row["human_id"] == human_id for row in tables["humans"]):
                raise ValidationError("Unknown export human_id")
            for table in ARCHIVE_TABLES:
                if "human_id" in table.c:
                    tables[table.name] = [
                        row for row in tables[table.name] if row["human_id"] == human_id
                    ]
            ids = {
                kind: {row["assessment_id"] for row in tables[table.name]}
                for kind, table in db.ASSESSMENTS.items()
            }
            tables["assessment_decisions"] = [
                row
                for row in tables["assessment_decisions"]
                if any(row[f"{kind}_id"] in ids[kind] for kind in ids)
            ]
            for name in ("mutation_events", "idempotency_keys"):
                tables[name] = [row for row in tables[name] if row["scope"] in ("god", human_id)]
            # Proposals may contain information about other users. Personal archives omit them.
            tables["proposals"] = []
        return canonical_json(
            {
                "schema_version": 1,
                "scope": "full" if human_id is None else "human",
                "human_id": human_id,
                "source": state,
                "methodology": asdict(graph.methodology),
                "tables": tables,
            }
        )


def _parse(payload):
    try:
        if isinstance(payload, str):

            def unique_object(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValidationError(f"Duplicate JSON key: {key}")
                    result[key] = value
                return result

            payload = json.loads(payload, object_pairs_hook=unique_object)
        # Copy caller data, and reject values such as NaN even if supplied as a dict.
        payload = json.loads(canonical_json(payload))
        if set(payload) != {
            "schema_version",
            "scope",
            "human_id",
            "source",
            "methodology",
            "tables",
        }:
            raise ValidationError("Unexpected archive fields")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValidationError("Unsupported archive schema")
        if payload["scope"] not in ("full", "human"):
            raise ValidationError("Unknown archive scope")
        source = payload["source"]
        if set(source) != {"id", "database_id", "revision", "configuration_hash"} or (
            type(source["id"]) is not int or source["id"] != 1
        ):
            raise ValidationError("Invalid source identity")
        uid(source["database_id"])
        if type(source["revision"]) is not int or source["revision"] < 0:
            raise ValidationError("Invalid source revision")
        Methodology(**payload["methodology"])
        tables = payload["tables"]
        if set(tables) != {table.name for table in ARCHIVE_TABLES}:
            raise ValidationError("Archive table set differs from schema")
        for table in ARCHIVE_TABLES:
            if not isinstance(tables[table.name], list):
                raise ValidationError("Archive tables must be row lists")
            for row in tables[table.name]:
                if not isinstance(row, dict) or set(row) != set(table.c.keys()):
                    raise ValidationError(f"Unexpected columns in {table.name}")
                for column in table.columns:
                    value = row[column.name]
                    if value is None:
                        if not column.nullable:
                            raise ValidationError(f"Null {table.name}.{column.name}")
                        continue
                    expected = column.type.python_type
                    if expected is float:
                        if type(value) not in (int, float):
                            raise ValidationError("Expected numeric score/confidence")
                    elif type(value) is not expected:
                        raise ValidationError(f"Invalid type for {table.name}.{column.name}")
                    if column.name.endswith("_at"):
                        timestamp(value)
                    if column.name == "score":
                        score(value)
                    if column.name in ("sequence", "revision") and value < (
                        0 if column.name == "revision" else 1
                    ):
                        raise ValidationError("Revision/sequence outside supported bounds")
                    if column.name in (
                        "concept_id",
                        "human_id",
                        "a_id",
                        "b_id",
                        "assessment_id",
                        "current_assessment_id",
                        "event_id",
                        "decision_id",
                        "proposal_id",
                    ):
                        if uid(value) != value:
                            raise ValidationError("IDs must use canonical UUID representation")
        return payload
    except (TypeError, KeyError, json.JSONDecodeError, AttributeError) as exc:
        raise ValidationError("Malformed graph archive") from exc


def _validate_semantics(payload):
    tables = payload["tables"]
    concept_map = {row["concept_id"]: row for row in tables["concepts"]}
    humans = {row["human_id"]: row for row in tables["humans"]}
    memberships = {(row["human_id"], row["concept_id"]): row for row in tables["human_concepts"]}
    god_edges = {(row["a_id"], row["b_id"]): row for row in tables["god_edges"]}
    if payload["scope"] == "human":
        human_id = uid(payload["human_id"])
        if set(humans) != {human_id}:
            raise ValidationError("Personal archive contains an incorrect set of humans")
        for table in ARCHIVE_TABLES:
            if "human_id" in table.c and any(
                row["human_id"] != human_id for row in tables[table.name]
            ):
                raise ValidationError("Personal archive contains another user's data")
    elif payload["human_id"] is not None:
        raise ValidationError("Full archive must not select a human_id")
    for row in tables["concepts"]:
        if (
            not row["canonical_name"].strip()
            or row["normalized_name"] != row["canonical_name"].casefold()
        ):
            raise ValidationError("Invalid normalized concept name")
    for row in tables["concept_aliases"]:
        if not row["alias"].strip() or row["normalized_alias"] != row["alias"].casefold():
            raise ValidationError("Invalid alias normalization")
    for row in tables["human_concepts"]:
        if row["active"] and (
            not humans.get(row["human_id"], {}).get("active")
            or not concept_map.get(row["concept_id"], {}).get("active")
        ):
            raise ValidationError("Active membership has inactive/missing dependencies")
    for row in tables["god_edges"]:
        if row["active"] and not all(
            concept_map.get(row[key], {}).get("active") for key in ("a_id", "b_id")
        ):
            raise ValidationError("Active God Brain edge has inactive/missing endpoints")
    for row in tables["human_edges"]:
        if row["active"] and (
            not god_edges.get((row["a_id"], row["b_id"]), {}).get("active")
            or not all(
                memberships.get((row["human_id"], row[key]), {}).get("active")
                for key in ("a_id", "b_id")
            )
        ):
            raise ValidationError("Active human edge has inactive/missing dependencies")
    for kind, (subject_table, keys) in db.SUBJECTS.items():
        observations = {row["assessment_id"]: row for row in tables[db.ASSESSMENTS[kind].name]}
        latest = {}
        decided = set()
        for decision in sorted(tables["assessment_decisions"], key=lambda row: row["sequence"]):
            assessment_id = decision[f"{kind}_id"]
            if assessment_id is None:
                continue
            if assessment_id not in observations or assessment_id in decided:
                raise ValidationError("Invalid or duplicate assessment decision")
            decided.add(assessment_id)
            observation = observations[assessment_id]
            canonical_json(json.loads(observation["provenance"]))
            if not observation["method"].strip():
                raise ValidationError("Assessment method is empty")
            if kind == "similarity" and json.loads(observation["provenance"]) is None:
                raise ValidationError("Similarity provenance is required")
            if decision["accepted"]:
                latest[tuple(observation[key] for key in keys)] = observation
        if decided != set(observations):
            raise ValidationError("All imported observations require a decision")
        for subject in tables[subject_table.name]:
            latest_observation = latest.get(tuple(subject[key] for key in keys))
            if latest_observation is None or (
                subject["current_assessment_id"] != latest_observation["assessment_id"]
                or subject["score"] != latest_observation["score"]
            ):
                raise ValidationError(
                    "Current score/pointer differs from latest accepted assessment"
                )
    methodology_hashes = set()
    for row in tables["methodology_versions"]:
        methodology = Methodology(**json.loads(row["configuration"]))
        if methodology.configuration_hash != row["configuration_hash"]:
            raise ValidationError("Methodology hash mismatch")
        methodology_hashes.add(methodology.configuration_hash)
    if Methodology(**payload["methodology"]).configuration_hash not in methodology_hashes:
        raise ValidationError("Active methodology is absent from archive")
    if (
        payload["source"]["configuration_hash"]
        != Methodology(**payload["methodology"]).configuration_hash
    ):
        raise ValidationError("Source configuration differs from active methodology")
    scopes = {
        "god": payload["source"]["revision"],
        **{key: row["revision"] for key, row in humans.items()},
    }
    event_revisions = {key: [] for key in scopes}
    for row in tables["mutation_events"]:
        if row["scope"] not in scopes:
            raise ValidationError("Event references unknown scope")
        canonical_json(json.loads(row["payload"]))
        event_revisions[row["scope"]].append(row["revision"])
    for scope, revision in scopes.items():
        # The first archive version retains all events; no compaction is allowed.
        ordered = sorted(event_revisions[scope])
        if len(ordered) != revision or any(
            value != index for index, value in enumerate(ordered, 1)
        ):
            raise ValidationError("Incomplete event history or invalid scope revision")
    from .proposals import validate_proposal

    for row in tables["proposals"]:
        validate_proposal(json.loads(row["payload"]))
        if row["status"] == "pending":
            if row["resolution"] is not None or row["resolved_at"] is not None:
                raise ValidationError("Pending proposal has resolution metadata")
        else:
            if row["resolved_at"] is None or row["resolution"] is None:
                raise ValidationError("Resolved proposal lacks resolution metadata")
            resolution = json.loads(row["resolution"])
            if not isinstance(resolution, dict):
                raise ValidationError("Invalid proposal resolution")
            if row["status"] == "accepted":
                if set(resolution) != {"results", "accepted_at"} or not isinstance(
                    resolution["results"], list
                ):
                    raise ValidationError("Invalid proposal acceptance record")
                timestamp(resolution["accepted_at"])
            elif row["status"] == "rejected":
                if (
                    set(resolution) != {"reason"}
                    or not isinstance(resolution["reason"], str)
                    or not resolution["reason"].strip()
                ):
                    raise ValidationError("Invalid proposal rejection record")
    observation_lookup = {
        kind: {row["assessment_id"]: row for row in tables[table.name]}
        for kind, table in db.ASSESSMENTS.items()
    }
    decision_lookup = {
        (kind, row[f"{kind}_id"]): row
        for row in tables["assessment_decisions"]
        for kind in db.ASSESSMENTS
        if row[f"{kind}_id"] is not None
    }
    for retry in tables["idempotency_keys"]:
        request, result = json.loads(retry["request"]), json.loads(retry["result"])
        kind = request["kind"]
        if kind not in db.ASSESSMENTS or not isinstance(result, str):
            raise ValidationError("Invalid idempotency request/result")
        observation = observation_lookup[kind].get(result)
        decision = decision_lookup.get((kind, result))
        if observation is None or decision is None:
            raise ValidationError("Idempotency result references a missing assessment")
        subject = {key: observation[key] for key in db.SUBJECTS[kind][1]}
        expected = dict(
            kind=kind,
            subject=subject,
            score=observation["score"],
            method=observation["method"],
            provenance=json.loads(observation["provenance"]),
            confidence=observation["confidence"],
            assessed_at=request.get("assessed_at"),
            accepted=decision["accepted"],
        )
        if (
            request != expected
            or retry["scope"] != subject.get("human_id", "god")
            or (
                expected["assessed_at"] is not None
                and expected["assessed_at"] != observation["assessed_at"]
            )
        ):
            raise ValidationError("Idempotency request does not match its assessment")


def _load(graph, payload):
    with graph.store.transaction(write=True) as connection:
        if any(
            connection.execute(select(table).limit(1)).first() is not None
            for table in (db.concepts, db.humans, db.events, db.proposals, db.origins)
        ):
            raise Conflict("Initial archive import requires an empty graph; merge is not implicit")
        # Cyclic subject/current-assessment references are deferred until commit.
        for table in ARCHIVE_TABLES:
            rows = payload["tables"][table.name]
            if table is db.methodologies:
                existing = set(connection.execute(select(table.c.configuration_hash)).scalars())
                rows = [row for row in rows if row["configuration_hash"] not in existing]
            if rows:
                # Initial subjects exist before their typed assessments; fill deferred
                # current pointers after the acceptance decisions have been inserted.
                if "current_assessment_id" in table.c:
                    rows = [{**row, "current_assessment_id": None} for row in rows]
                connection.execute(insert(table), rows)
        for kind, (table, keys) in db.SUBJECTS.items():
            for row in payload["tables"][table.name]:
                connection.execute(
                    update(table)
                    .where(*(table.c[key] == row[key] for key in keys))
                    .values(current_assessment_id=row["current_assessment_id"])
                )
        connection.execute(
            update(db.state)
            .where(db.state.c.id == 1)
            .values(
                revision=payload["source"]["revision"],
                configuration_hash=(Methodology(**payload["methodology"]).configuration_hash),
            )
        )
        connection.execute(
            insert(db.origins).values(
                import_id=uid(),
                source_database_id=payload["source"]["database_id"],
                source_god_revision=payload["source"]["revision"],
                archive_hash=hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
                imported_at=timestamp(),
            )
        )


def import_json(graph, payload, *, dry_run=False) -> dict:
    from .engine import MemoryGraph

    if type(dry_run) is not bool:
        raise ValidationError("dry_run must be a boolean")
    if not dry_run and graph.store._current.get() is not None:
        raise Conflict("Archive import owns its transaction; call it outside graph.transaction()")
    payload = _parse(payload)
    try:
        _validate_semantics(payload)
        # Validate actual database constraints in a disposable store before touching the target.
        with MemoryGraph() as validation_graph:
            _load(validation_graph, payload)
    except (Conflict, KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"Archive validation failed: {exc}") from exc
    report = {
        "valid": True,
        "dry_run": dry_run,
        "rows": {key: len(value) for key, value in payload["tables"].items()},
        "source_database_id": payload["source"]["database_id"],
    }
    if not dry_run:
        with graph.store._lock:
            _load(graph, payload)
            graph._methodology = Methodology(**payload["methodology"])
    return report


def graphml(snapshot) -> str:
    """Interoperability projection; use JSON archives for complete history."""
    namespace = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", namespace)

    def tag(name):
        return f"{{{namespace}}}{name}"

    root = ET.Element(tag("graphml"))
    fields = [
        ("label", "node", "string"),
        ("description", "node", "string"),
        ("understanding", "node", "double"),
        ("similarity", "edge", "double"),
        ("familiarity", "edge", "double"),
        ("traversal_cost", "edge", "double"),
        ("revision", "graph", "string"),
        ("methodology", "graph", "string"),
    ]
    for name, scope, kind in fields:
        ET.SubElement(
            root, tag("key"), {"id": name, "for": scope, "attr.name": name, "attr.type": kind}
        )
    graph = ET.SubElement(root, tag("graph"), {"id": snapshot.scope, "edgedefault": "undirected"})

    def data(parent, key, value):
        if value is not None:
            ET.SubElement(parent, tag("data"), {"key": key}).text = str(value)

    data(graph, "revision", canonical_json(asdict(snapshot.revision)))
    data(graph, "methodology", canonical_json(asdict(snapshot.methodology)))
    for key, node in sorted(snapshot.nodes.items()):
        element = ET.SubElement(graph, tag("node"), {"id": key})
        for name in ("label", "description", "understanding"):
            data(element, name, getattr(node, name))
    for (a, b), edge in sorted(snapshot.edges.items()):
        element = ET.SubElement(graph, tag("edge"), {"source": a, "target": b, "id": f"{a}:{b}"})
        for name in ("similarity", "familiarity"):
            data(element, name, getattr(edge, name))
        if edge.in_human:
            data(element, "traversal_cost", snapshot.edge_cost(a, b))
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


@dataclass(frozen=True)
class SparseExport:
    matrix: object
    concept_ids: tuple[str, ...]
    semantics: str
    revision: Revision
    configuration_hash: str


def sparse_matrix(snapshot, *, semantics="similarity") -> SparseExport:
    if semantics not in ("similarity", "familiarity", "cost"):
        raise ValidationError("Unknown matrix semantics")
    if semantics != "similarity" and snapshot.scope != "human":
        raise ValidationError("Familiarity/cost matrix requires human scope")
    try:
        from scipy.sparse import csr_matrix
    except ImportError as exc:
        raise ImportError("Install memory-graph[sparse] for CSR export") from exc
    ids = tuple(sorted(snapshot.nodes))
    indexes = {key: index for index, key in enumerate(ids)}
    rows, columns, values = [], [], []
    for (a, b), edge in sorted(snapshot.edges.items()):
        value = (
            edge.similarity
            if semantics == "similarity"
            else edge.familiarity
            if semantics == "familiarity"
            else snapshot.edge_cost(a, b)
        )
        rows.extend([indexes[a], indexes[b]])
        columns.extend([indexes[b], indexes[a]])
        values.extend([value, value])
    return SparseExport(
        csr_matrix((values, (rows, columns)), shape=(len(ids), len(ids))),
        ids,
        semantics,
        snapshot.revision,
        snapshot.methodology.configuration_hash,
    )
