# VR tutoring mathematics

The canonical graph engine owns and versions all traversal mathematics. This backend delegates to
`memory_graph.shortest_path` and records its full path explanation, revision and methodology.

`w_h(a,b) = 1 / (s(a,b) * (u_h(a) + u_h(b)) + epsilon)`.

Human edge familiarity is retained in explanations and history but excluded from this baseline cost.
The active engine currently uses `baseline_v1`, version 1, with epsilon `1e-9`. The LUCID planning
document instead proposed `human_dijkstra_v1` with epsilon `1e-6`; this implementation deliberately
does not overwrite the memory engine's chosen configuration. Health/path responses expose actual
values; requests for an inactive formula ID fail validation. This cost is a modeling assumption,
not an empirically proven measure of educational effort.

The deterministic vector lesson uses `proj_u(v) = ((u dot v)/(u dot u)) u`, rejecting zero `u`.
For `u=(3,0)`, `v=(2,3)`, the displayed projection is `(2,0)` and dot product is 6.
Local slider angle values are degrees; vector components are dimensionless canonical lesson units.
Sliders are declarative bindings for the future client, not executable expressions.
User understanding changes only on explicit self-report. No automatic scoring or decay is added.
