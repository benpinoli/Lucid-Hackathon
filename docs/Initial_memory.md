# Project Specification: God Brain and Human Brain Knowledge Graph Infrastructure

## 1. Objective

Build a robust Python infrastructure for representing, storing, updating, and traversing two related knowledge graphs:

1. **God Brain:** A universal concept graph representing inherent similarities between topics, independent of any individual.
2. **Human Brain:** A personalized graph representing an individual's understanding of concepts and familiarity with relationships between concepts.

Implement a derived weighted graph on which Dijkstra's shortest-path algorithm can operate to find efficient conceptual routes between topics.

The system must be modular, persistent, testable, scalable, and independent of any frontend. Use mathematically precise graph representations with a clear distinction between stored properties and dynamically computed traversal costs.

---

## 2. Mathematical Definitions

### 2.1 God Brain

Define the God Brain as an undirected weighted graph

\[
\boxed{G_{\mathrm{god}}=(V_{\mathrm{god}},E_{\mathrm{god}},s)}
\]

where:

- \(V_{\mathrm{god}}\) is the set of all known concepts.
- \(E_{\mathrm{god}}\subseteq \{\{a,b\}:a,b\in V_{\mathrm{god}},a\neq b\}\) is the set of undirected conceptual relationships.
- \(s:E_{\mathrm{god}}\rightarrow[1,10]\) assigns an inherent similarity score to each relationship.

For two concepts \(a,b\), define

\[
s(a,b)=s(\{a,b\}).
\]

Since the graph is undirected,

\[
\boxed{s(a,b)=s(b,a).}
\]

Higher similarity values indicate stronger inherent conceptual relationships.

The God Brain does **not** store human understanding levels. Its similarity scores are independent of individual users.

### 2.2 Human Brain

For each human \(h\), define

\[
\boxed{G_h=(V_h,E_h,u_h,f_h)}
\]

where:

- \(V_h\subseteq V_{\mathrm{god}}\) is the set of concepts represented in the user's knowledge graph.
- \(E_h\) is the set of undirected relationships represented in the user's knowledge graph.
- \(u_h:V_h\rightarrow[1,10]\) measures the human's understanding of each concept.
- \(f_h:E_h\rightarrow[1,10]\) measures the human's familiarity with the connection between two concepts.

The two human-specific functions measure different things:

\[
\boxed{u_h(a)=\text{understanding of concept }a}
\]

\[
\boxed{f_h(a,b)=\text{familiarity with the relationship }a\leftrightarrow b}
\]

Because edges are undirected,

\[
f_h(a,b)=f_h(b,a).
\]

For the initial implementation, scores are recorded directly. Do not automatically decay, increase, or otherwise modify stored scores over time.

Every assessment should be stored with a timestamp so that historical assessments can be recovered.

The latest accepted score is the current score.

### 2.3 Relationship Between the Graphs

All nodes must have globally stable concept identifiers shared between the God Brain and Human Brain.

A concept appearing in both graphs must refer to the same underlying concept.

For the initial implementation, require

\[
\boxed{E_h\subseteq E_{\mathrm{god}}.}
\]

This guarantees that every human edge has a defined God Brain similarity score.

If the user or an LLM attempts to create an edge that does not exist in the God Brain, the system must explicitly handle it by validating or creating the corresponding God Brain edge. Never silently assign an arbitrary similarity score.

The God Brain and Human Brain must remain separately stored.

---

## 3. Dijkstra Traversal Cost

Define a derived traversal-cost function

\[
\boxed{
w_h:E_h\rightarrow\mathbb R_{>0}
}
\]

using the following formula:

\[
\boxed{
w_h(a,b)
=
\frac{1}{
s(a,b)\big(u_h(a)+u_h(b)\big)+\varepsilon
}
}
\]

where:

- \(s(a,b)\) is the God Brain similarity between concepts \(a\) and \(b\).
- \(u_h(a)\) is the human's understanding of concept \(a\).
- \(u_h(b)\) is the human's understanding of concept \(b\).
- \(\varepsilon>0\) is a configurable numerical regularization constant.

All terms must be positive, so

\[
\boxed{w_h(a,b)>0.}
\]

The traversal-cost function is symmetric:

\[
\boxed{w_h(a,b)=w_h(b,a).}
\]

### 3.1 Interpretation

The traversal cost decreases as:

1. The inherent similarity between the two concepts increases.
2. The human's understanding of either endpoint increases.

Holding other variables fixed,

\[
\frac{\partial w_h}{\partial s}
=
-\frac{u_h(a)+u_h(b)}
{\left[s(u_h(a)+u_h(b))+\varepsilon\right]^2}<0.
\]

Similarly,

\[
\frac{\partial w_h}{\partial u_h(a)}
=
-\frac{s(a,b)}
{\left[s(a,b)(u_h(a)+u_h(b))+\varepsilon\right]^2}<0.
\]

The same result holds for \(u_h(b)\).

Thus the desired monotonic properties follow directly from the formula.

### 3.2 Human Edge Familiarity

The human edge familiarity function

\[
f_h:E_h\rightarrow[1,10]
\]

must be stored and accessible but **must not be incorporated into the initial Dijkstra formula**.

We intentionally preserve

\[
w_h(a,b)
=
\frac{1}{s(a,b)(u_h(a)+u_h(b))+\varepsilon}.
\]

The implementation should permit future experimentation with alternative cost functions incorporating \(f_h(a,b)\), but this must not change the baseline function.

---

## 4. Shortest-Path Optimization

For a starting concept \(a\in V_h\) and a target concept \(b\in V_h\), let

\[
\mathcal P_h(a,b)
\]

be the set of all valid paths connecting \(a\) to \(b\) in the Human Brain.

For a path

\[
P=(v_0,v_1,\ldots,v_k),
\]

define its total cost as

\[
\boxed{
C_h(P)
=
\sum_{i=0}^{k-1}
\frac{1}{
s(v_i,v_{i+1})
\left[u_h(v_i)+u_h(v_{i+1})\right]
+\varepsilon
}
}
\]

The optimal path is

\[
\boxed{
P_h^*
\in
\underset{P\in\mathcal P_h(a,b)}
{\operatorname{argmin}}
\ C_h(P)
}
\]

Implement Dijkstra's algorithm to calculate the minimum-cost path.

Because all weights are strictly positive, Dijkstra's nonnegative-edge-weight requirement is satisfied.

**Important:** Multiple paths may have equal minimum cost. Therefore, the solution need not be unique. Provide a deterministic tie-breaking policy and document it.

If no path exists, return a structured unreachable result rather than an exception representing an algorithmic failure.

### 4.1 Dynamic Weight Calculation

Traversal weights must be **derived**, not independently stored as canonical values.

When a node understanding score or God Brain similarity changes, all affected traversal costs must reflect the new values.

Avoid maintaining stale duplicate weights.

Caching is permitted only with explicit invalidation logic or version-based cache keys.

### 4.2 Multiple Starting Concepts

The infrastructure must also support finding the optimal path from a set of starting concepts

\[
S\subseteq V_h
\]

to a target concept \(t\).

Compute

\[
\boxed{
P^*
\in
\underset{s\in S,\ P\in\mathcal P_h(s,t)}
{\operatorname{argmin}}
C_h(P)
}
\]

Implement multi-source Dijkstra, or an equivalent algorithm with a virtual source connected to all starting nodes by zero-cost edges.

The virtual source must not be persisted as a real concept.

---

## 5. Infrastructure Requirements

### 5.1 Graph Engine

Implement:

- God Brain graph creation, modification, and deletion.
- Human Brain graph creation, modification, and deletion.
- Stable globally unique concept identifiers.
- Undirected weighted graph relationships.
- Node understanding scores.
- Human edge familiarity scores.
- God Brain inherent similarity scores.
- Derived traversal costs.
- Single-source Dijkstra.
- Multi-source Dijkstra.
- Path cost inspection and explanation.
- Graph connectivity and component analysis.
- Neighbor lookup and subgraph extraction.
- Efficient sparse graph representations.

Do not use dense adjacency matrices as the primary storage structure.

Provide optional sparse adjacency matrix export using SciPy, including independently selectable matrices for inherent similarity, human familiarity, and traversal cost.

### 5.2 Persistence

Use SQLite initially, with a schema designed to permit migration to PostgreSQL later.

Suggested logical tables:

**Concepts**
- concept_id
- canonical_name
- description
- created_at
- updated_at

**God Edges**
- concept_a_id
- concept_b_id
- similarity_score
- provenance
- created_at
- updated_at

**Human Concepts**
- human_id
- concept_id
- current_understanding
- created_at
- updated_at

**Human Edges**
- human_id
- concept_a_id
- concept_b_id
- current_familiarity
- created_at
- updated_at

**Assessment History**
- assessment_id
- human_id
- subject_type
- concept_a_id
- concept_b_id (nullable for node assessments)
- score
- assessed_at
- assessment_method

Implement proper foreign keys, uniqueness constraints, transactions, indexes, and schema migrations.

Canonicalize undirected edges so the database never stores both \((a,b)\) and \((b,a)\) as separate relationships.

### 5.3 API

Provide a clean typed Python interface supporting operations such as:

```python
god = GodBrain()

god.add_concept("Linear Algebra")
god.add_concept("Quantum Mechanics")

god.connect(
    "Linear Algebra",
    "Quantum Mechanics",
    similarity=8
)

human = HumanBrain(god_brain=god)

human.add_concept(
    "Linear Algebra",
    understanding=9
)

human.add_concept(
    "Quantum Mechanics",
    understanding=3
)

human.connect(
    "Linear Algebra",
    "Quantum Mechanics",
    familiarity=5
)

path = human.shortest_path(
    "Linear Algebra",
    "Quantum Mechanics"
)

print(path.concepts)
print(path.total_cost)
```

These calls illustrate the desired interface. Design production-quality class signatures, identifiers, and errors as appropriate.

### 5.4 Future LLM Integration

Design explicit interfaces for later connecting an LLM capable of:

- Generating new concepts and candidate God Brain relationships.
- Estimating inherent similarities.
- Constructing topic-specific subgraphs.
- Initializing user concepts from previous coursework.
- Proposing connections between established and unfamiliar subjects.
- Suggesting assessments of node understanding and edge familiarity.

Separate LLM-generated proposals from verified graph mutations.

Preserve provenance and confidence metadata without conflating estimated confidence with similarity or understanding scores.

Do not implement an entire LLM orchestration framework unless necessary for a clean extension point.

### 5.5 Import and Export

Support JSON serialization and deserialization of:

- God Brain graph data.
- Human Brain graph data.
- Historical assessments.
- Configuration and parameter versions.

Also provide GraphML or another widely supported graph interchange format.

Ensure serialization round trips preserve graph semantics, IDs, scores, and timestamps.

---

## 6. Validation and Testing

Create comprehensive automated tests.

At minimum verify:

1. Edge symmetry in both graphs.
2. Valid understanding scores between 1 and 10.
3. Valid similarity scores between 1 and 10.
4. Valid familiarity scores between 1 and 10.
5. Every Human Brain node references an existing God Brain concept.
6. Every Human Brain edge references an existing God Brain edge.
7. Traversal-cost formula correctness.
8. Strict positivity of computed weights.
9. Traversal-cost symmetry.
10. Weight monotonicity with respect to understanding and similarity.
11. Weight recomputation after assessment changes.
12. Correct shortest paths on manually calculated example graphs.
13. Correct multi-source shortest paths.
14. Unreachable-target handling.
15. Deterministic tie-breaking.
16. Persistence and serialization round trips.
17. Transactional integrity and duplicate-edge prevention.
18. Sparse graph performance at large node and edge counts.

Use a trusted reference implementation, such as NetworkX, to verify shortest-path results on generated test graphs.

Include unit tests, integration tests, and performance benchmarks.

Do not claim the proposed edge-cost function has been validated as an optimal educational learning-cost model. It is an initial modeling assumption whose mathematical and empirical behavior can be evaluated later.

---

## 7. Engineering Deliverables

Produce a working, documented Python project with:

1. A clean package structure.
2. Typed graph data models.
3. God Brain and Human Brain implementations.
4. Dynamic traversal-cost calculations.
5. Dijkstra and multi-source Dijkstra implementations.
6. SQLite persistence and migrations.
7. Assessment history recording.
8. Sparse matrix export.
9. Import/export capabilities.
10. Automated tests and benchmarks.
11. Runnable examples.
12. A README with setup, usage, mathematical definitions, architecture, and design decisions.

Maintain a dedicated `METHODOLOGY.md` explaining all mathematical assumptions and formula choices.

Also maintain a machine-readable `methodology.json` containing the active traversal formula identifier, version, parameter values, score bounds, tie-breaking policy, and any changes to the mathematical model.

Do not silently modify the mathematical model while implementing or refactoring.

## 8. Implementation Strategy

First design the data model, invariants, API, and database schema.

Then implement the minimal graph engine and prove its correctness using automated tests.

Next add persistence, history, serialization, and performance optimizations.

Finally add extension interfaces for future LLM integration and potential Obsidian import.

Prioritize correctness, transparency, modularity, and reproducibility over frontend design.

**The initial goal is a fully functioning mathematical knowledge-graph engine, not a graphical application.**
