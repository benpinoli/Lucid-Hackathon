# Mathematical methodology

## Canonical graph model

The God Brain is an undirected simple sparse graph of globally identified concepts. Every God Brain edge has an explicitly supplied inherent similarity `s` in `[1,10]`, independent of personal scores. A Human Brain is a subset of those concepts and edges; its nodes carry understanding `u` and its edges carry familiarity `f`, each in `[1,10]`. All scores may be fractional. No self-loops, duplicate reversed edges, inferred missing scores, or automatic score changes are permitted.

Edges are canonicalized by lexicographic ordering of lowercase hyphenated UUID strings. A missing edge is distinct from a low similarity. Missing personal membership is distinct from low understanding. Unknown personal values remain null in comparison exports.

## Baseline formula

Formula identifier: **baseline_v1**, version **1**.

```text
w_h(a,b) = 1 / (s(a,b) * (u_h(a) + u_h(b)) + epsilon)
epsilon = 1e-9 (default)
```

Familiarity is not an input to this baseline calculation, although it is retained in snapshots and path explanations. Epsilon must be finite and positive. All score inputs must be finite and in bounds. Boolean values are invalid scores. Every computed traversal cost must be finite and strictly positive.

With default epsilon, edge costs range from `1/(200+epsilon)` to `1/(2+epsilon)`. Symmetry follows from undirected similarity and addition of endpoint understanding. Increasing similarity or either understanding decreases cost because the denominator increases. Epsilon is retained to match the specification even though the score bounds already keep the denominator positive.

Total path cost is the sum of its edge costs. Dijkstra minimizes that sum; it does not minimize hops, maximize familiarity, or recommend a curriculum independently of this model. Favoring familiar concepts may produce paths that differ from an effective teaching sequence. The educational validity of this formula remains an empirical question.

## Path semantics and deterministic choices

Only active edges and endpoints in the selected Human Brain participate. Multiple starting concepts are initialized at distance zero; no virtual source is persisted. An empty starting set is invalid. A valid target already in the starting set yields that target alone at zero cost. A valid disconnected target yields structured `unreachable` with no path and null total cost. Invalid IDs/memberships raise domain errors.

The deterministic tie policy is `sorted_ids_first_discovery_strict_improvement`:

1. Deduplicate and sort source IDs.
2. Iterate each adjacency list in sorted ID order.
3. Order heap entries by `(distance, concept_id)`.
4. Replace a predecessor only if the newly computed distance is strictly smaller.
5. Preserve the first discovered predecessor on exactly equal floating-point distances.

This guarantees a deterministic result for the same graph snapshot and arithmetic implementation. It does not specify the lexicographically smallest complete path. Costs are Python double-precision floating-point computations; no arbitrary tolerance is used to redefine equal costs or optimality. Tests use approximate comparisons to an independent reference implementation where arithmetic order can differ.

## Assessment and revision semantics

An observation has an immutable ID, score, assessment time, recording time, method, provenance, and optional confidence. Acceptance is a separate immutable decision with a globally ordered database sequence. The latest accepted decision for a subject determines its current score, regardless of the observation's wall-clock assessment timestamp. Rejected observations do not replace current scores. A correction creates another observation/decision. No decay or automatic reinforcement is applied.

Similarity history is retained alongside personal understanding/familiarity histories. Logical deletion retains audit evidence. The first release provides score history and revision-bound explanations, but no arbitrary historical-time traversal API.

A path is calculated from one consistent read snapshot and records database identity, God Brain revision, Human Brain revision/identity, formula/version, epsilon, configuration hash, and all per-edge score/assessment inputs. Costs are derived after loading; canonical duplicate traversal weights are never stored. If either graph changes, a new snapshot derives new costs. A viewer rejects overlays with mismatched revisions or configuration.

## Configuration and model changes

The root `methodology.json` is the human-reviewable baseline configuration; the identical packaged resource supplies the default for new databases. It contains the expression, bounds, epsilon, tie policy and an empty initial changelog. `Methodology` validates supported values. Configuration identity is the SHA-256 hash of canonical sorted JSON containing `formula_id`, `version`, normalized `epsilon`, and `tie_breaking`. The model ID/version fixes the expression, bounds and changelog; incompatible values are rejected. Full configuration metadata is persisted immutably and included in exports/results. Tests keep both configuration files synchronized.

An explicit `Methodology(epsilon=...)` may be passed when opening a database; it is persisted as the default for subsequent openings. An existing open instance retains its chosen immutable configuration. JSON imports restore their active configuration. Different configurations are distinguishable even when graph revisions match.

Changing the baseline expression, score bounds, or tie policy requires a new supported formula identifier/version, documentation, tests, and migration/configuration compatibility handling. The current implementation rejects unknown policies. The `CostPolicy` protocol establishes the algorithm-facing interface for that expansion. Any future Dijkstra policy must produce finite positive weights; a policy with negative weights requires a different algorithm and an explicit model change. Confidence never substitutes for similarity, understanding, or familiarity.
