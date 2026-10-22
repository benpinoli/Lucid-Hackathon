# Benchmark observations

Measured locally on 2026-10-22 using Windows 11, Python 3.14.4, SQLAlchemy 2.1.6 and Alembic 1.20.1. CPU identification: Intel64 Family 6 Model 189 Stepping 1, GenuineIntel. These are individual development runs, not service-level guarantees or statistically controlled comparisons.

All runs use `baseline_v1`, epsilon `1e-9`, configuration hash `9908a71a51530edfdbe0c9c27e1f636027bd73abed55cb9917637393946d0bcf`. Database benchmarks use seed 42 and include score observations, acceptance decisions, revision events, and current projections, rather than only inserting an adjacency list.

| Database workload | 1k nodes / 5k edges, uniform | 10k nodes / 50k edges, uniform | 1k nodes / 5k edges, hub |
| --- | ---: | ---: | ---: |
| Python allocation tracking | Enabled | Disabled | Disabled |
| Public-API write batch | 50.35 s | 208.36 s | 20.60 s |
| Personal snapshot load | 0.246 s | 1.317 s | 0.061 s |
| Snapshot SQL executions, including BEGIN | 7 | 7 | 7 |
| Path computation after loading | 0.094 s | 0.056 s | 0.042 s |
| Projection computation | 0.123 s | 0.210 s | 0.038 s |
| JSON export | 1.286 s | 3.027 s | 0.295 s |
| JSON archive size | 15.44 MB | 154.77 MB | 15.44 MB |
| SQLite size after close | 19.29 MB | 195.40 MB | 19.28 MB |
| Peak Python allocations | 64.77 MB | Not tracked | Not tracked |

The hub projection correctly reported truncation at its configured 500-node / 2,000-edge limits. A path's search extent depends on topology and scores, so these path times are not directly comparable by node count. Allocation tracking substantially changes timing overhead.

A subsequent untraced disconnected 1k/5k run returned structured `unreachable` as expected: snapshot load 0.120 s, path search 0.041 s, neighbor lookup 0.0016 s, and JSON export 0.278 s. Only the first half of that graph has edges; the target lies in the isolated half.

The first uniform database runs used an `OR` endpoint neighbor query. The 10k run measured 0.170 s for that lookup. Inspection led to a `UNION ALL` implementation that independently uses the forward and reverse endpoint indexes; a query-plan regression test now checks both branches avoid edge-table scans. The subsequent hub run measured 0.0024 s for a lookup with 999 neighbors. Because the datasets differ, this is evidence that indexed lookup works, not a controlled before/after speedup claim.

## Larger algorithm-only workload

`python benchmarks/snapshot_scale.py --nodes 100000` constructs a deterministic sparse ring with five offsets per node:

- 100,000 nodes, 500,000 undirected edges, 1,000,000 adjacency entries.
- Snapshot construction: 2.13 seconds.
- Dijkstra search to the opposite side of the ring: 4.06 seconds.
- Returned path: 10,000 edges, verified against the analytically expected hop count and cost.

This exercises sparse in-memory construction and traversal only. It does not measure SQLite persistence, assessment history, JSON archives, or peak memory at that size. Database behavior at 100k/500k remains unmeasured.

## Practical implications

The sparse read/traversal foundation is usable at the measured sizes. Audited per-operation writes are the main measured ingestion cost, and history archives grow faster than the bare graph. A future bulk-ingestion API should preserve atomicity, validation, and audit semantics while reducing repeated service queries. Profile that path before adding caches or changing the storage model.

Full archives are materialized in memory. The 154.77 MB archive alone does not capture peak export/import working memory, so these measurements do not justify a large-database archival memory claim. Streaming archives and larger-than-memory query adapters remain future work. Default snapshot caps are resource guards rather than measured capacity promises.

Reproduce database runs with `benchmarks/sparse_graph.py`; use `--trace-memory` explicitly when measuring allocations. The harness also provides `--shape disconnected`. Machine-readable local output files named `benchmark-results*.json` are ignored by Git.
