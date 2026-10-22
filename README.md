# Memory Graph

A typed Python engine for a shared concept graph (**God Brain**) and separate personal knowledge graphs (**Human Brains**). SQLite stores concepts, relationships, assessments, and audit history. The engine has no frontend dependency.

## Setup

Python 3.11 or newer is required. From PowerShell:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev,sparse]"
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe examples/basic.py
```

The existing project virtual environment is ready to use. `requirements-dev.lock` records the exact development environment verified on Windows with Python 3.14; install it with `pip install -r requirements-dev.lock` when reproducing that environment. The package declares compatible dependency ranges for other supported Python versions. SciPy is optional at runtime; install `.[sparse]` to use CSR exports.

## Use the engine

```python
from memory_graph import MemoryGraph

with MemoryGraph.open("sqlite:///memory.db") as graph:
    with graph.transaction():
        algebra = graph.god.add_concept("Linear Algebra")
        quantum = graph.god.add_concept("Quantum Mechanics")
        graph.god.connect(
            algebra.id, quantum.id, similarity=8,
            provenance={"source": "reviewed curriculum"},
        )
        human = graph.create_human()
        human.add_concept(algebra.id, understanding=9)
        human.add_concept(quantum.id, understanding=3)
        human.connect(algebra.id, quantum.id, familiarity=5)

    path = human.shortest_path(algebra.id, quantum.id)
    print(path.concepts, path.total_cost)
    print(path.to_dict())  # Scores, assessment IDs, revisions, and methodology

    human.assess_understanding(algebra.id, 7, expected_revision=human.revision)
    human.assess_familiarity(algebra.id, quantum.id, 6)
    print(human.multi_source_shortest_path([algebra.id, quantum.id], quantum.id))
```

The example creates new objects each time. Reopen a known user with `graph.human(human_id)` and retrieve concepts with `graph.god.get_concept(concept_id)`. Save IDs in your application. Labels and aliases can be used for convenience, but ambiguous names raise `AmbiguousConcept`; duplicate creation is a conflict rather than an implicit upsert. A rename preserves identity.

Each public mutation is atomic. `graph.transaction()` makes a group of mutations atomic, with savepoints so a failed individual call cannot leave partial changes if its exception is caught. `expected_revision` applies to the service's own scope: the God Brain revision for God mutations, or that Human Brain's revision for personal mutations. Proposals can check both scopes together.

## Mathematical behavior

For a personal edge connecting `a` and `b`:

```text
cost(a,b) = 1 / (similarity(a,b) * (understanding(a) + understanding(b)) + epsilon)
epsilon = 1e-9
```

All three stored score types accept finite fractional values from 1 to 10. Familiarity is available for inspection but does not enter this formula. Costs are computed from immutable read snapshots; they are not canonical database columns. A changed understanding or similarity is reflected in the next snapshot/path query.

Single-source and multi-source Dijkstra operate only on personal edges. Results distinguish `reachable` and `unreachable`; unreachable cost is `None`. A target in the source set returns a one-node zero-cost path. Unknown concepts and empty source sets are input errors. Sources, neighbors, and heap ties use sorted UUIDs; equal-cost alternatives retain the first discovered predecessor. See [METHODOLOGY.md](METHODOLOGY.md) for exact assumptions and tie rules.

## History and lifecycle

```python
assessment_id = human.assess_understanding(
    algebra.id, 6,
    method="quiz", provenance={"quiz_id": "quiz-42"}, confidence=0.8,
    assessed_at="2026-10-22T12:00:00-06:00",
    idempotency_key="quiz-42-algebra",
)
history = graph.history(
    kind="understanding", human_id=human.human_id, concept=algebra.id,
)
```

Every initial and subsequent score has an immutable observation and acceptance decision, with timezone-aware timestamps. The latest **accepted decision sequence** determines the current value. An assessment made in the past but accepted now becomes current. Use `accepted=False` to record a rejected suggestion without changing the current score. A correction is a new observation/decision; existing observations and decisions cannot be edited. `confidence` is metadata, independent of the score.

Assessment idempotency keys are scoped to God Brain or a particular Human Brain. Repeating the same request/key returns its assessment ID without a new event; changing its contents raises `Conflict`. If an expected revision is supplied, that precondition is checked before processing the retry.

Use `disconnect`, `delete_concept`, and `graph.delete_human` for logical deletion. God Brain deletion is restricted while active personal records depend on it; `cascade=True` explicitly deactivates those dependencies in the same transaction. Restoration is explicit with `restore_concept`, `restore_edge`, or `restore_human`. Restoring a parent does not automatically restore children. History remains available for inactive records. Physical purging and arbitrary historical-time traversal are not exposed in this release.

## Visualization contract

```python
snapshot = human.snapshot()
projection = snapshot.project(
    roots=[algebra.id], hops=2, max_nodes=500, max_edges=2000,
    min_similarity=3,
)
payload = projection.to_dict()
# Recompute the path against the same revision before making an overlay.
path = human.shortest_path(algebra.id, quantum.id)
overlay = projection.path_overlay(path)
```

The versioned payload contains stable node/link IDs, labels, descriptions, membership flags, independent score fields, traversal costs, revision/configuration identity, filters, limits, counts, and explicit truncation. Degrees refer to the filtered snapshot before neighborhood truncation. Every returned link has both endpoints in the payload. An overlay fails clearly if its path is stale or is not fully visible.

`graph.snapshot()` selects God Brain scope. `graph.snapshot(human_id=human.human_id, scope="comparison")` overlays personal information on the universal graph; unknown personal scores and costs remain `None`. Personal/comparison projections support `membership="human"` or `"unrepresented"`, `min_understanding`, `min_familiarity`, and `max_cost`. Null personal values fail personal-score filters.

To expand a graph, take a fresh snapshot and call `project(new_roots, expected_revision=previous_revision, ...)`. A changed revision raises `Conflict`, prompting a refresh. A future web or desktop renderer can consume this contract directly for force-directed layout, click-to-expand, score coloring, and highlighted paths. Keep display coordinates and pinning in separate documents keyed by concept ID. An Obsidian Markdown adapter can be added independently. This release supplies the data interface, not the graphical viewer.

## Interchange and sparse analysis

```python
from pathlib import Path

archive = graph.export_json()  # God Brain, all humans, history, proposals, configuration
Path("archive.json").write_text(archive, encoding="utf-8")
personal_archive = graph.export_json(human_id=human.human_id)

with MemoryGraph("sqlite:///restored.db") as restored:
    restored.import_json(archive, dry_run=True)
    restored.import_json(archive)

Path("personal.graphml").write_text(human.snapshot().graphml(), encoding="utf-8")
csr = human.snapshot().sparse_matrix(semantics="cost")  # or similarity / familiarity
print(csr.concept_ids, csr.matrix)
```

JSON is the lossless archive: IDs, scores, timestamps, observations, decisions, events, configuration versions, and proposal records are preserved. Import validates both semantics and actual database constraints before writing; the target must be empty. Import owns its transaction and must be called outside `graph.transaction()`. `dry_run` validates the archive without applying it; actual application additionally checks that the target is empty. Merging nonempty graphs is deliberately unsupported until an explicit conflict-resolution policy exists.

A personal archive includes the complete universal dependency graph and history plus that user's personal records; it omits proposals because those may contain other users' information. It is not a minimal universal subgraph export. Import creates a fresh database identity and records source identity, revision, archive hash, and import time. Revisions and historical sequence values within the archive are preserved. An imported active methodology remains the default on reopening.

GraphML is an undirected interoperability projection, not a complete audit backup. CSR exports include the ordered ID mapping, revision, configuration hash, and score semantics. Missing edges and the diagonal are structural zeros. Similarity is available for God or human scope; familiarity and cost require human scope.

## LLM extension boundary

Implement `ProposalProvider.propose(context)` to return a JSON-compatible candidate, then submit it through `graph.proposals.submit(payload)`. Submission does not alter graph scores or topology. Explicit `accept(proposal_id)` validates expected revisions and applies the entire proposal transactionally through ordinary service methods. `reject(proposal_id, reason="...")` retains it as rejected evidence. Acceptance is idempotent by proposal ID.

See `tests/test_projections_and_proposals.py` for runnable proposal examples. The schema requires source, evidence, confidence, expected revisions, and an allowlisted list of commands referencing stable IDs. It supports candidate concepts/relationships, initial personal membership, and score assessments. Provider/network work belongs outside database transactions. There is no autonomous model orchestration in this package.

## Architecture, storage, and expansion

| Module | Responsibility |
| --- | --- |
| `domain.py` | Immutable typed records, validation, errors, baseline cost policy |
| `engine.py` | God/Human services, transactional mutations, indexed queries, snapshots |
| `storage.py` | SQL connections, explicit transactions, SQLite configuration, migration runner |
| `schema_v1.py`, `migrations/` | Frozen initial schema and ordered Alembic upgrades |
| `algorithms.py` | Pure sparse computations independent of SQL |
| `projections.py` | Renderer-neutral bounded node/link exports and path overlays |
| `interchange.py` | Validated JSON, GraphML and optional CSR |
| `proposals.py` | Provider contract, untrusted candidate validation and explicit acceptance |

SQLite foreign keys, checks, composite uniqueness, and triggers protect references, edge ordering, active dependencies, score pointers, and immutable audit data. God and human data occupy separate tables. Both endpoints have indexed access paths. WAL allows coherent readers alongside a writer; writes use `BEGIN IMMEDIATE`, a bounded busy timeout, and optimistic revision checks. A store serializes its own connection access; independent stores coordinate through SQLite. Use the public service API for mutations so revisions and audit events are always maintained.

Opening a database applies Alembic upgrades transactionally. Revision `0001` is the frozen initial relational schema; `0002` adds SQLite guards. Future upgrades must be new migration files, never edits to released schema/migrations. Downgrades intentionally fail rather than silently deleting audit data; restore a verified backup if necessary. Transaction handling follows [SQLAlchemy's SQLite guidance](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#transactions-with-sqlite-and-the-sqlite3-driver); future table changes can use [Alembic batch migrations](https://alembic.sqlalchemy.org/en/latest/batch.html).

The implementation uses SQLite in-memory mode for fast tests and examples, avoiding a second mutable graph implementation with different persistence rules. Public domain records and pure snapshot algorithms form the backend boundary; SQL-specific application queries stay in `engine.py`. PostgreSQL requires a new adapter, equivalent lifecycle/audit guards, concurrency tests, and migration parity. It is not enabled by changing the database URL alone. `CostPolicy`, `SnapshotReader`, and `ProposalProvider` expose focused extension contracts; registering alternative persistent formula implementations requires a future explicit schema/configuration change. Unknown formula identifiers currently fail validation.

## Performance and limits

Snapshots use sparse adjacency lists with `O(V+E)` memory; Dijkstra uses `O((V+E) log V)` time. Personal snapshot loading uses a fixed number of queries rather than querying per edge relaxation. No derived-cost cache is maintained. A snapshot freezes both graph revisions and all scores used for its calculation.

Default snapshot caps are **100,000 nodes / 500,000 edges**. These are safety limits, not a performance guarantee. Override with `human.snapshot(max_nodes=..., max_edges=...)`, or use `snapshot_max_nodes` / `snapshot_max_edges` on shortest-path calls. `max_visited` separately bounds Dijkstra search work. Exceeding a limit returns `ResourceLimit`, never a partial result labeled optimal. Viewer projection budgets limit output after snapshot loading; they do not make loading a huge global graph cheap.

Full JSON archive export/import currently materializes history in memory; large archives may require substantially more memory than graph traversal. Streaming archives, SQL-backed neighborhood extraction for graphs above snapshot limits, revision deltas, authentication, and a graphical viewer are future work. A `human_id` scopes personal APIs but is not authentication; hosted applications must authorize it at their API boundary.

```powershell
.venv/Scripts/python.exe benchmarks/sparse_graph.py --nodes 1000 --edges 5000 --trace-memory
.venv/Scripts/python.exe benchmarks/sparse_graph.py --nodes 10000 --edges 50000
.venv/Scripts/python.exe benchmarks/sparse_graph.py --nodes 1000 --edges 5000 --shape hub
.venv/Scripts/python.exe benchmarks/sparse_graph.py --nodes 1000 --edges 5000 --shape disconnected
.venv/Scripts/python.exe benchmarks/snapshot_scale.py --nodes 100000
```

The seeded harness measures public-API writes, snapshot load, neighbor queries, paths, projections, export, and database size. `--trace-memory` adds Python allocation tracking and substantially affects timings; traced and untraced runs should not be compared as equivalent. See [benchmark observations](docs/Benchmark_observations.md) for measured results and practical limits.

## Verification

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests examples benchmarks
.venv/Scripts/python.exe -m build --wheel --no-isolation
```

Tests cover score and formula properties, NetworkX reference comparisons on generated graphs, deterministic ties, multiple sources, unreachable targets, cross-graph invariants, rejected/out-of-order assessments, idempotent retries, rollback, concurrent revision conflicts, snapshot isolation, archive corruption/round trips, prior-version upgrades, GraphML/CSR, and visualization/proposal contracts.
