# Memory graph implementation plan

Status: the first SQLite implementation is complete in `src/memory_graph`; see [README](../README.md) for the implemented API and [benchmark observations](Benchmark_observations.md) for measured limits. Source of requirements: [Initial_memory.md](Initial_memory.md).

Implementation choices: SQLite in-memory mode replaces a separate mutable in-memory repository, so tests exercise production constraints. Domain records and pure snapshot algorithms are storage-independent; SQLAlchemy Core application queries remain together in the service layer until a second backend justifies extracting more repository interfaces. The graphical viewer, PostgreSQL adapter, archive merge/streaming, arbitrary-time traversal, and registered alternative cost formulas remain future extensions. Their contracts and compatibility considerations are documented below and in the README.

## 1. Outcome and scope

Build a frontend-independent Python package with a persistent God Brain, separate per-user Human Brains, reproducible path calculations, and a stable graph projection interface for a future Obsidian-like viewer.

The first release includes the graph engine, SQLite migrations, assessment history, import/export, tests, benchmarks, and runnable examples. A graphical application, LLM orchestration, and automatic score changes are outside this release. Visualization readiness is part of the first release: stable IDs, bounded neighborhood queries, versioned node/link exports, filtering, and path overlays must be available and tested.

The mathematical model remains exactly as specified:

`cost(a, b) = 1 / (similarity(a, b) * (understanding(a) + understanding(b)) + epsilon)`

Only Human Brain edges are traversable. Familiarity is stored and exposed but does not affect the baseline cost. Unknown concepts or missing scores receive no invented defaults. The formula is a modeling assumption, not an empirically validated measure of learning difficulty.

## 2. Architecture and package boundaries

Use one Python package with explicit dependencies pointing inward toward domain models. Start with a single application and database, rather than independent services.

```text
src/memory_graph/
  domain/          # IDs, validated scores, records, errors, policies
  application/     # GodBrain, HumanBrain, assessment and proposal services
  algorithms/      # Sparse snapshots, Dijkstra, components, neighborhoods
  persistence/     # Repository interfaces, SQL implementation, transactions
  interchange/     # Versioned JSON, GraphML, optional SciPy export
  projections/     # Renderer-neutral node/link and path-overlay payloads
  proposals/       # Validated proposal contracts and acceptance workflow
  methodology/     # Formula registry and configuration validation
tests/
  unit/
  integration/
  property/
  fixtures/
benchmarks/
examples/
migrations/
METHODOLOGY.md
methodology.json
```

Use typed immutable records for returned data and protocols for storage and cost policies. Use SQLAlchemy with Alembic for the initial SQL adapter and migrations; keep SQL-specific objects out of public domain records. NetworkX serves as a test oracle, not the canonical graph store. SciPy is an optional export dependency. Lock concrete dependency versions when scaffolding the implementation.

An explicit unit of work owns the transaction shared by all repositories involved in a mutation. Keep abstractions narrow: concept/edge access, assessments, snapshots, and proposals; avoid a generic repository framework.

## 3. Domain contracts and invariants

### Identity and membership

- Generate stable UUID concept IDs in the application. Persist a canonical lowercase representation with consistent ordering across databases. Renaming a concept never changes its ID.
- Labels are display data, not identifiers. Allow ambiguous names; provide indexed normalized lookup and aliases. A name convenience API must return an ambiguity error when it cannot resolve exactly one concept.
- Each Human Brain has an explicit stable `human_id`. Its concept membership references the God Brain concept catalog. Its edges reference both of its own endpoint memberships and a God Brain edge.
- Store one undirected relationship per endpoint pair. Normalize to `a_id < b_id` before every lookup or write; enforce this order and uniqueness in SQL. Reject self-loops.
- Scores are finite numeric values in `[1, 10]`, including fractional assessments. Reject booleans, NaN, infinity, and out-of-range input. Epsilon must be finite and strictly positive; computed costs must also be finite and strictly positive.
- A membership or human edge must have an accepted initial understanding or familiarity assessment. A God Brain edge requires an explicitly supplied, validated similarity and provenance.

### Updates, history, and deletion

- An assessment is an immutable observation with a stable ID, subject, score, UTC `assessed_at`, method, provenance, and optional confidence. Record server acceptance time and a transactionally assigned acceptance sequence separately.
- Current score means the latest accepted assessment, ordered by acceptance sequence, not client timestamp. A late-arriving old observation does not silently rewrite history according to wall-clock ordering. Accepting it as the new current score is explicit.
- Store observations and acceptance decisions separately so a correction or rejection never destroys the original record. Derive current scores from accepted decisions, with transactionally maintained current projections for fast reads. Each current projection points to its accepted assessment.
- Maintain similarity revision history as well as human assessments. This enables historical traversal explanations; human history alone cannot reconstruct earlier paths.
- Every committed mutation increments the applicable God Brain or Human Brain revision in the same transaction. Failed writes increment nothing.
- Use logical deletion for ordinary graph operations, retaining identity and audit history. Active queries exclude deleted records. Removal of a God Brain edge or concept is blocked while active Human Brain references exist unless an explicit cascading deactivation operation is requested. Deactivate dependent records atomically; never leave an active human edge with an inactive endpoint or God Brain edge.
- Restoration revalidates dependencies and is explicit. Physical purging is a later administrative feature, with separate retention rules.

## 4. Persistence design

God and human data use separate tables within one database. Sharing a transaction boundary allows cross-graph invariants to be enforced without distributed coordination.

| Table | Key and responsibility |
| --- | --- |
| `concepts` | `concept_id`; canonical name, description, timestamps, deletion state |
| `concept_aliases` | Concept reference and normalized searchable alias; aliases need not be globally unique |
| `god_edges` | Composite key `(a_id, b_id)`; current similarity, accepted revision reference, provenance, deletion state |
| `humans` | `human_id`; lifecycle metadata and current graph revision |
| `human_concepts` | Composite key `(human_id, concept_id)`; current understanding and accepted assessment reference |
| `human_edges` | Composite key `(human_id, a_id, b_id)`; current familiarity and accepted assessment reference |
| `understanding_assessments` | Immutable typed node observations; FK to human concept membership |
| `familiarity_assessments` | Immutable typed edge observations; composite FK to human edge |
| `similarity_assessments` | Immutable God Brain edge observations and provenance |
| `assessment_decisions` | Acceptance/rejection/correction events with deterministic acceptance order |
| `graph_state` | God Brain revision and database identity |
| `mutation_events` | Ordered graph mutations, including lifecycle and descriptive changes |
| `methodology_versions` | Immutable formula identifiers, parameters, versions, and configuration hash |
| `proposals` | Candidate changes, evidence, confidence, status, and resolution references |

Typed assessment tables avoid a polymorphic subject field whose references cannot be protected by ordinary foreign keys. Expose a unified assessment history record in Python and JSON. Specify typed decision references with foreign keys and a check requiring exactly one assessment subject. Validate that current assessment pointers belong to the same subject; use composite foreign keys where feasible and transactional validation for remaining invariants.

Enforce SQL checks for score bounds, canonical edge ordering, and non-self edges. Add foreign keys from human edges to God Brain edges and to both endpoint memberships. Enable SQLite foreign keys on every connection. Use restrictive physical-delete policies for referenced audit records.

Index both endpoint access patterns: the primary key covers `a_id`, and a second index covers `b_id`. For human edges, prefix both indexes with `human_id`. Add history indexes on subject plus acceptance order, alias/name lookup indexes, and mutation-event revision indexes. Neighbor retrieval uses the two endpoint indexes, not a table scan or dense matrix.

Score updates insert the observation and decision, update the current projection, append the mutation event, and advance revisions in one transaction. Idempotency keys prevent duplicate application of retried assessment/proposal requests. Optimistic expected-revision checks protect users from overwriting concurrent edits; return a structured conflict rather than silently retrying a changed intent.

For SQLite, configure a bounded busy timeout and use short write transactions, with WAL where supported. Scope reads to a consistent transaction. Keep database-specific pragmas isolated. PostgreSQL migration includes explicit type mappings, acceptance-order allocation, locking/isolation behavior, migration tests, and constraint parity; using an ORM alone does not guarantee portability.

## 5. Public Python API and failure semantics

Construct services through an explicit engine/context rather than implicitly creating an in-memory graph or a global database:

```python
with MemoryGraph.open("sqlite:///memory.db") as graph:
    linear = graph.god.add_concept("Linear Algebra")
    quantum = graph.god.add_concept("Quantum Mechanics")
    graph.god.connect(linear.id, quantum.id, similarity=8, provenance=source)

    human = graph.create_human()
    human.add_concept(linear.id, understanding=9, method="self_report")
    human.add_concept(quantum.id, understanding=3, method="self_report")
    human.connect(linear.id, quantum.id, familiarity=5, method="self_report")
    result = human.shortest_path(linear.id, quantum.id)
```

Explicit APIs include score assessment, rename, deactivate, restore, neighbors, connected components, bounded subgraph extraction, single/multi-source shortest paths, path explanation, history, import/export, and visualization projection. Duplicate creation is a documented conflict; explicit upsert and idempotency operations handle retries without silently accepting different data.

Path results contain status, selected source, target, ordered concept IDs, edge breakdowns, total cost, graph revision pair, formula ID/version, epsilon, and configuration hash. Each edge explanation records similarity, both understanding values, familiarity for inspection, relevant assessment IDs, and computed cost.

Use structured `unreachable` results for valid disconnected endpoints. Distinguish malformed input, unknown IDs, concepts outside the human graph, missing God Brain edges, conflicts, and resource limits through typed domain errors. A missing God Brain edge returns a specific error or a proposal workflow; it never creates a similarity implicitly. Empty source sets are invalid. A valid target included in the source set yields a zero-cost, one-node path.

## 6. Consistent graph computation

Load an immutable sparse adjacency snapshot from a consistent read transaction. The snapshot includes active human membership, human edges, God Brain similarities, current understanding/familiarity, and the exact revision pair. Compute outside the database transaction after loading. This prevents one path from mixing scores from different commits and avoids an SQL query per algorithm relaxation.

Start without a persistent weight cache. Derive each cost from the snapshot. If profiling justifies snapshot caching, key it by database identity, `human_id`, God Brain revision, Human Brain revision, formula ID/version, and configuration hash. Never combine independently cached slices from different revisions.

Implement heap-based multi-source Dijkstra and delegate single-source traversal to it. Sort sources and neighbors by canonical ID. Use heap entries ordered by `(distance, concept_id)`, discard stale entries, and replace predecessors only on a strictly smaller cost. On exactly equal computed costs, preserve the first discovered predecessor. This is the documented deterministic policy for the same snapshot and arithmetic implementation; it does not promise the lexicographically smallest full path. Do not use an arbitrary floating-point tolerance to change which path is optimal.

Snapshot storage is `O(V_h + E_h)` and Dijkstra is `O((V_h + E_h) log V_h)`. Components and bounded breadth-first neighborhood extraction use the same sparse representation. Add configurable resource limits and structured limit errors; a partial traversal must never be reported as an optimal complete result. Larger-than-memory traversal is a future adapter, not an implied capability of this first implementation.

`methodology.json` defines `baseline_v1`, the formula expression, proposed epsilon `1e-9`, score bounds, source/target rules, and tie-breaking policy. Validate this file against a schema and record its hash on outputs. Future policies implement a cost interface and declare their inputs; Dijkstra rejects nonpositive or nonfinite costs. Introducing a new policy creates a new identifier/version and never changes `baseline_v1` silently.

## 7. Obsidian-like visualization readiness

Provide a renderer-neutral `GraphProjection` contract now. A later browser or desktop renderer can use force-directed layout without reading SQL or reimplementing graph rules.

The projection supports God Brain, one Human Brain, and an explicit comparison view. Each node includes stable ID, label, description summary, membership flags, nullable understanding, degree with a documented scope, and optional metadata. Each link includes stable identity derived from canonical endpoint IDs and graph scope, `source`, `target`, similarity, nullable familiarity, and nullable traversal cost. God Brain-only edges have no personal traversal cost. Scores and cost retain separate field names and semantics.

Every response includes schema version, revision pair, projection scope, filters, limits, counts, and an explicit truncation flag. Neighborhoods accept root IDs, hop radius, node/edge budgets, and deterministic ordering. Return only links with both endpoints present. Pagination or expansion tokens bind to revisions; reject stale tokens and request a fresh projection rather than combining snapshots.

Required viewer-support operations:

- Search concepts and center on a selected stable ID.
- Retrieve a bounded ego graph and expand neighbors on demand.
- Filter by membership and explicit score ranges; unknown personal values remain null.
- Retrieve connected components and isolated nodes within a stated scope.
- Overlay a path by ordered node/edge IDs and attach its cost explanations. Reject or refresh overlays whose revisions differ from the projection.
- Fetch full details/history separately so overview payloads remain small.

Store future coordinates, pins, colors, and collapse state in a separate per-user layout document keyed by stable IDs and layout version. Force-layout distances are display choices; they must not overwrite similarity or Dijkstra cost. Keep canonical labels as data and safely escape them in a future renderer.

The first release uses versioned snapshots. A later transport can expose the same contracts over HTTP and add revision-based deltas from `mutation_events`, with a defined resynchronization response when history has been compacted. A future Obsidian Markdown adapter can preserve IDs in frontmatter and express links with wikilinks; it is separate from the visualization contract and canonical JSON archive.

## 8. Import, export, and proposals

Canonical JSON archives include schema version, stable IDs, graph scopes, current projections, full assessment/decision history, timestamps, provenance, revisions, methodology versions, and configuration. Validate the entire import and cross-graph references before committing. Initial imports are atomic, with a dry-run report and explicit ID-conflict policy; never silently overwrite or merge concepts based on labels. A human-only archive declares its God Brain dependencies and fails clearly when they are absent.

Recompute and verify current projections against imported acceptance history. Preserve imported historical ordering within its scope; any merge into an existing graph assigns local acceptance order through an explicit merge policy while retaining source ordering metadata. Renumbering local mutation revisions must retain original export provenance rather than claiming the two databases share a revision identity.

GraphML is an interoperability projection, with declared node/link attribute types and undirected edges. Canonical JSON remains the lossless archive for history, proposals, and configuration. Sparse SciPy exports return CSR data plus ordered concept-ID mapping, graph scope, revisions, and weight semantics. Offer separate similarity, familiarity, and cost matrices; absent edges are structural zeros and the diagonal is zero. God scope permits similarity; human scope permits all three over human edges.

LLM adapters return schema-validated proposals only. Proposals include candidate concepts/edges/assessments, evidence, model/source identity, confidence, and expected revisions. An acceptance service resolves identities, checks invariants, and applies approved changes transactionally through the same application APIs. Confidence never becomes a graph score. LLM work and network calls happen outside write transactions.

## 9. Implementation sequence and acceptance gates

| Phase | Deliverables | Gate before advancing |
| --- | --- | --- |
| 1. Contracts | Package scaffold, domain records, errors, identity policy, methodology files, schema design | Invariants and public operation semantics documented; score/config validation tests pass |
| 2. Graph correctness | In-memory repository for tests/examples, sparse snapshots, baseline cost, single/multi-source traversal, inspection and components | Hand-calculated fixtures and reference/property tests pass, including deterministic ties and unreachable cases |
| 3. Durable graph | SQLite schema/migrations, transaction layer, assessments and decisions, revisions, lifecycle operations | Repository contract suite passes against SQLite; reopen, rollback, FK, retry, and concurrent-update tests pass |
| 4. Interchange and viewer foundation | JSON archive, GraphML, optional CSR export, bounded projections, path overlays | Lossless JSON round trips and projection/revision/limit contracts pass; documented projection example runs |
| 5. Extension and hardening | Proposal acceptance interface, benchmarks, query profiling, documentation and runnable demos | No unresolved correctness defects; reproducible benchmark results and explicit supported limits published |

Finish each phase with a runnable example and reviewable changes. PostgreSQL adapter work and the graphical viewer follow these gates; they do not delay the first usable SQLite engine.

## 10. Verification and performance plan

Use unit tests for cost calculations and validation; property tests for symmetry, monotonicity, canonicalization and randomized mutation sequences; integration tests for persistence, migrations, transactions, and serialization. Use NetworkX to compare optimal costs on generated graphs; compare paths only where the optimum is unique because tie policies may differ.

Essential scenarios include all 18 checks in the source specification, plus:

- Reject nonfinite input and invalid epsilon; changing familiarity leaves baseline costs unchanged.
- Two users share concept identity and similarity but cannot read or mutate each other's personal graph through scoped APIs.
- Equal timestamps, out-of-order observations, rejected assessments, correction decisions, and idempotent retries preserve deterministic current scores.
- Concurrent readers see coherent revision pairs; competing score updates report conflicts; a forced failure rolls back history, current score, event, and revision together.
- Reversed duplicate edges, missing memberships, invalid imported references, and invalid lifecycle operations cannot bypass invariants through SQL/import paths.
- Deactivation/restoration retains historical evidence and excludes inactive dependencies from traversal.
- Source equals target, duplicate sources, empty sources, disconnected components, exact ties, and near-equal costs follow the documented contract.
- Projection truncation is explicit, edge endpoints always exist in the payload, and stale pagination/path overlays are detected.
- CSR node mappings reconstruct the intended symmetric graphs; JSON preserves timestamps, IDs, accepted history, and methodology.
- Migrations work on a fresh database and representative previous-version fixtures.

Benchmark reproducible sparse graphs at proposed tiers of 1k/5k, 10k/50k, and 100k/500k nodes/edges. Measure database size, write batch time, neighborhood query count/latency, snapshot load time, path time, export time, and peak memory. Include skewed hub degrees and disconnected graphs, not just uniform random graphs. Record seed, hardware, dependency versions, and methodology hash. Set latency/memory budgets after the first measured baseline; do not promise unmeasured scale. Add regression thresholds only on a controlled benchmark runner.

## 11. Defaults to review during phase 1

Proceed with these defaults unless product needs change: fractional scores, UUID identity, nonunique labels with explicit disambiguation, acceptance-order current scores, one God Brain per database, multiple isolated Human Brains, logical deletion with restricted dependencies, and snapshot-based visualization exports.

Clarify historical-query scope during schema implementation: the proposed audit/revision records support reconstructing past graphs, but a public arbitrary-time traversal API is a later feature. Similarly, `human_id` scopes data but is not authentication; a future hosted API must derive it from authenticated identity and enforce authorization at its boundary.

The initial implementation follows these correctness gates. Further work should start from the implemented contracts and measured limitations rather than silently changing the mathematical model.
