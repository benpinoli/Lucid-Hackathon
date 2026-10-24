# LUCID VR backend progress

Frontend follow-up, 2026-10-22: a connected desktop/WebXR starter is now implemented.
See [frontend verification](FRONTEND.md) and [setup/runbook](../frontend/README.md).
The backend-only findings below remain historical; their frontend gaps are superseded
where listed in the follow-up. Physical headset acceptance is still pending.

Date: 2026-10-22. Backend-only implementation requested alongside a concurrent memory agent.
Commit: uncommitted. Repository started with no commits and untracked memory work; no branch
switch, staging or commit was performed to avoid capturing or disturbing that work.

## Phase 0 — Repository and backend baseline

Status: PARTIAL (backend gate passes; frontend/hardware gates pending).
Files implemented: `backend/app`, isolated `backend/.venv`, `backend/requirements.lock`,
`backend/README.md`, demo seed command, separate backend CI workflow, architecture/methodology docs.
The root memory package, dependency environment and migration files were preserved.

Commands run from repository root:

```powershell
$env:PYTHONPATH='src;.'
backend/.venv/Scripts/python.exe -m pytest -c backend/pytest.ini backend/tests -q
.venv/Scripts/python.exe -m ruff check backend/app backend/tools backend/tests
backend/.venv/Scripts/python.exe -m backend.tools.schemas --check
.venv/Scripts/python.exe -m pytest tests -q
backend/.venv/Scripts/python.exe -m backend.tools.benchmark
```

Test results: 17 backend tests pass, Ruff passes, both generated schemas match;
48 existing memory tests pass. Health and startup lifespan exercised through FastAPI TestClient.
Dependency warning: current Starlette TestClient deprecates httpx in favor of httpx2; tests pass.
Actual Python: 3.14.4, Windows 11 build 26200. CI targets Python 3.12; remote CI has not run.
No server, credentials or data were published.

Measured perf: 1,000 in-process deterministic six-object text turns, no provider/network:
plan/validate/commit p50 **0.0463 ms**, p95 **0.0651 ms**, p99 **0.1332 ms**.
This is server-only microbenchmark evidence, not XR or end-to-end latency evidence.
Frame time, visible response latency, GPU memory, physical Quest/browser/thermal/network configuration:
**unmeasured**. Proposed target remains Quest 3; no physical device selected/tested.

Known gaps: no frontend/IWSDK starter, local HTTPS termination, emulator or HUD in this backend scope.
Next exact tasks: renderer owner should consume `shared/schemas/scene.v1.schema.json`, build the
five supported primitives and local projection slider, connect authenticated WS, then record baseline frame timings.

## Phase 1 — Safe scene contract and retained server state

Status: PARTIAL (server validation/state gates pass; rendering gates pending).
Files implemented: `backend/app/scene.py`, `shared/schemas/scene.v1.schema.json`, schema generator.
Versioned strict schemas cover axes, arrows, curves, labels and sliders; replace/create/patch/remove.
State enforces scene scope, sequence, base revision, committed epoch, event ID replay and object budgets.
Unknown parameters, arbitrary URLs/code primitives, nonfinite/bool numerics, oversize events,
duplicate objects and missing patch targets are tested. Failed operations leave retained state unchanged.
500 replacements keep six objects and bound replay memory at 256 entries.

Commands/test results: same passing commands above. GPU disposal, 100-event rendering, offline FPS
and local slider-to-visible timing are **unmeasured**. No GPU resource claim follows from server object counts.
Next exact tasks: implement the client registry, bounded structural apply queue, resource pool and
strict schema validation, then test 500 scene changes in the actual renderer.

## Phase 2 — Integration with concurrently implemented memory engine

Status: PARTIAL (adapter verified; memory engine owned by the other agent).
Files implemented: `backend/app/service.py`, user-scoped HTTP graph endpoints, adapter tests.
No new graph schema, migrations or alternate Dijkstra implementation were created.
Uses stable concept IDs and revision-bound immutable snapshots; bounded cache includes both graph
revisions and methodology hash. Exact reciprocal cost and familiarity exclusion remain engine-owned.
Active `baseline_v1`/epsilon `1e-9` are reported instead of overriding them with LUCID plan defaults.
Explicit assessments use atomic graph history/idempotency/revision APIs. Cross-user requests fail.

Evidence: 48 memory tests pass; backend tests additionally check exact cost, universal-score cache
invalidation, personal-score updates, retained history, stale revision conflicts and retry idempotency.
10k/50k graph latency: **unmeasured by this backend session**.
Next exact tasks: coordinate a pinned stable memory API after that agent finishes; run its graph
benchmark and compare actual results with LUCID targets without changing formula semantics.

## Phase 3 — Deterministic lesson and authenticated transport

Status: PARTIAL (backend text slice working; visual gate pending).
Files implemented: `backend/app/main.py`, `learning.py`, `service.py`,
`shared/schemas/learning-plan.v1.schema.json`, manifests, tests and standalone demo seed.
Text input produces validated authored scenes with semantic teaching beats: dot product,
Dijkstra's algorithm ("dijkstra"/"shortest path") and the mean value theorem ("mean value"/"mvt").
Unknown topics receive a safe whiteboard fallback. Optional graph context uses explicit stable IDs.
Auth is token-to-provisioned-human mapping; lessons have scoped ownership. WS verifies Origin and
first-message credential; no token is put into the URL. Reconnect returns compact state. Interrupt
advances epoch without clearing objects. Invalid/stale commands return recoverable errors.
HTTP bodies, WS commands, lesson counts, nodes, replay memory and cache entries are bounded.

Evidence: integration tests cover HTTP auth, WS auth/origin rejection, lesson ownership, stale turns,
interruption, reconnect equality, schema drift, invalid packets and oversized requests.
Known gaps: turns return an atomic scene replacement with beat metadata; timed incremental beat
delivery and audio-confirmed cues are not yet implemented. Each initiating HTTP/WS caller receives
its own result; other sockets explicitly request snapshots. Lessons are process-local, one worker only.
Current microbenchmark numbers are listed under Phase 0. Receipt-to-visible response is **unmeasured**.
Next exact tasks: connect renderer and text UI; apply shared schemas and scene epoch guards;
then implement cancellable beat scheduling and audio playout acknowledgments.

## Later phases

Phase 4 voice: PARTIAL. OpenAI Realtime over WebRTC with a backend-minted ephemeral credential
(`POST /api/voice/sessions`; `OPENAI_API_KEY`, `LUCID_VOICE_MODEL`, `LUCID_VOICE_NAME`). The model's
only tool is `show_lesson`; the client reads authored beats one response at a time and highlights each
beat on `output_audio_buffer.started` (playback-confirmed cue). Barge-in drops queued beats.
Unit-tested with recorded provider events; NOT yet verified against the live provider or on a headset.
Pending: push-to-talk, transcript display, audio/visual skew metrics. Text mode is unchanged without a key.
Voice turn-taking fix (first live test was choppy and cut questions/answers short): one
continuous response per tutor reply instead of one per beat; playback-clock highlights with
self-calibrating rate; mic gated while any tutor audio plays (echo ignored), opt-in voice barge-in,
VR trigger/Interrupt button to cut in; semantic VAD eagerness low; near-field noise reduction;
explicit echo cancellation. Unit-tested with recorded event sequences; needs a second live test.
Conversational tutor (dynamic VR flow): voice/typed actions goal, answer, confused, question,
continue, explore. Frontier-search starting check, silent baseline-Dijkstra route, diagram for every
explanation (authored diagram lessons for all non-recipe curriculum topics; Claude diagram lessons
otherwise), graded spoken checks as the only score source, confusion -> deeper explanation or
prerequisite detour, Claude-created concepts written to the shared graph via accepted proposals.
New scene primitives expr_curve/expr_arrow/expr_point with param sliders; safe expression language
in Python and TypeScript with shared parity fixtures. Tested without a provider end to end and with
the Claude author through a mocked transport. NOT yet run against live Claude or OpenAI, nor on a
headset. Pending: XR-native answer input other than voice, transcript display, spaced review.
Phase 6 learning goals: goal text -> curriculum concept (aliases, Claude fallback); diagnostic
quiz -> Human Brain assessments; route = engine baseline Dijkstra from known concepts (>= 7) or
cluster foundations; route map scene; per-step lessons; self-ratings update understanding and edge
familiarity and recompute the route. Curriculum synced into the God Brain at startup. Tested in
pytest (routes, costs, idempotent sync, atomic/stale writes, prerequisite guard) and a Playwright
goal flow. Pending: God Brain expansion for topics outside the curriculum (Claude proposals via
the engine's proposal API), voice-driven goals, spaced review.
Phase 6 (partial, Level 1 planner): Claude selects recipe + typed parameters via structured output
(`backend/app/planner.py`); builders compute geometry, numbers and narration, validate view/scene
budgets, and Dijkstra beats come from the algorithm trace; MVT points are solved exactly (property
tested). One corrective retry, then keyword fallback. Tested through the real SDK with a mocked
transport; NOT yet run against the live API. Pending: show a placeholder scene while planning
(Stage 0), graph-aware bridge selection, free-form sketches for topics without a recipe.
Phase 5: vector projection, Dijkstra (cross-checked by Bellman-Ford) and mean value point have golden numeric checks; wave packet/barrier recipes remain pending.
Phase 6: explicit user-scoped graph context and assessment loop exist; automated bridge selection pending.
Phase 7: physical headset, thermal, network failure and renderer measurements remain unmeasured.
Phase 8: outside the initial backend slice. No MVP completion claim is made.
