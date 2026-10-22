# LUCID: Low-Latency Voice-to-AR/VR Knowledge Tutor

**Agent execution specification | Version 1.0 | 2026-10-22**

> **Mission.** Build a responsive AI tutor that listens to a user, chooses an explanation using the God Brain / Human Brain knowledge graph, and incrementally constructs mathematically correct interactive AR/VR scenes *while it speaks*. Optimize for stable headset frame rate, low perceived response latency, testable physics, and graceful degradation. **The output must be runnable software, not a diagram or prototype-only pseudocode.**

## 0. EXECUTION DIRECTIVES FOR THE CODING AGENT

1. **Work vertically, not layer-by-layer.** Deliver a complete path `spoken/text query → graph → explanation → validated scene command → visible animation → user feedback → graph update` before expanding primitive counts or architecture.
2. **Follow the phase gates in order.** After each phase run its tests, record benchmark results, and update `docs/PROGRESS.md`. Do not announce a phase complete without its acceptance evidence.
3. **Keep XR render execution isolated from inference, networking, asset loading, graph IO and heavy simulation.** Rendering must keep working during network loss or 20-second inference stalls.
4. **LLMs select from safe, deterministic actions.** They do not send executable JavaScript, GLSL, arbitrary URLs, Python or free-form math code to be executed on the headset.
5. **Prioritize user-perceived latency:** launch an appropriate cached visual immediately, stream visual *deltas* as the lesson evolves, and never wait for a full lesson plan to render the first scene.
6. **No premature infrastructure.** No microservices, Kafka, vector DB, full spatial world editor, custom physics engine, or image-to-3D generation in the MVP. Add only when profiling or requirements justify it.
7. **Document the graph equations exactly.** Do not quietly change the user's shortest-path cost rule. Preserve source scores, historical assessments, and formula version metadata.
8. **Ship both desktop and headset paths** using a shared renderer and shared scene contract. A desktop emulator is essential for developer throughput; headset testing is essential for performance truth.
9. **Measure instead of claiming.** The numeric budgets below are acceptance targets to profile on reference hardware, not externally guaranteed performance.
10. **If work exceeds one coding session**, commit a runnable phase, record exact unfinished tasks and next commands in `docs/PROGRESS.md`, and resume from there. Never replace implementation with another high-level plan.

---

## 1. PRODUCT SCOPE AND MVP DEFINITION

### 1.1 Core user experience

1. User launches a browser-first immersive lesson (desktop emulator or compatible Quest headset).
2. User speaks: “Why does a quantum wave packet spread?”
3. Voice response begins quickly, without waiting for a complete lesson script.
4. The first useful visual appears immediately from a local recipe: axes + Gaussian envelope.
5. Backend queries knowledge graph, selects known conceptual bridges (e.g., Fourier components), and streams a validated sequence of scene changes.
6. The XR scene adds component sinusoids, a sum, time controls, and animated dispersion; the spoken explanation and visual beats are synchronized.
7. User interrupts: “Show me the separate waves.” The old planned beats are cancelled, but the existing scene and render loop remain alive. The renderer updates to the new focus.
8. User changes wave width with a slider; the visualization updates locally at frame speed, without network round trips.
9. The tutor asks understanding 1–10. The user's score is stored with a timestamp; prior scores remain immutable; no time decay.

### 1.2 Vertical-slice lessons (ordered)

**Lesson A (required first):** 2D vector projection and dot product. Tests text/voice, graph path lookup, axes/arrows/labels, slider, scene update, and understanding assessment.

**Lesson B (required second):** Gaussian wave packet and Fourier components. Tests procedural wave rendering, mathematical correctness, precise parameters, animation, interruption, and playback cue timing.

**Lesson C (required third):** 1D finite potential barrier with reflected/transmitted components and adjustable width. Tests nontrivial physics, real-time parameters, and a second teaching recipe.

**Not in MVP:** unconstrained 3D video generation, arbitrary executable Python/JS from the model, physically simulated full classrooms, native Unity port, photorealistic assets for all lessons, in-headset code editing, automatic skill decay, LLM-modified graph weights without user approval.

---

## 2. FIXED TECHNOLOGY STACK

| Concern | Decision | Why / constraint |
| --- | --- | --- |
| XR/frontend | **TypeScript + Vite + Meta IWSDK + Three.js** | Meta-supported WebXR flow, built-in desktop emulator, spatial UI, Three.js procedural geometry. Pin exact dependency versions in lockfile. |
| XR modes | Desktop first, then WebXR immersive VR and AR passthrough when available | Feature detect with `navigator.xr.isSessionSupported`; never assume AR support. |
| Render loop | IWSDK/Three.js-managed WebXR animation loop | No backend or LLM actions in frame callback; enqueue changes separately. |
| Client state | Lightweight typed store + scene entity registry | Low overhead; avoid rerendering app-wide React trees per frame. |
| Backend | Python 3.12+ + FastAPI + Pydantic v2 + uvicorn | Existing graph code compatibility, typed WS/HTTP contracts. |
| Graph algorithm | NetworkX initially + optimized in-memory adjacency representation later if profiling requires | Correctness first, Dijkstra reference implementation and sparse export. |
| Storage | SQLite + WAL (single-process demo) with explicit migration path to PostgreSQL | Simple local development and immutable assessment history. Do not share SQLite across multiple independent writers without a plan. |
| Voice | WebRTC real-time speech-to-speech provider, **with an adapter interface** | Direct low-latency browser audio path; optional server sideband for privileged tools. Configure actual provider/model by env. |
| Scene transport | One persistent WebSocket, JSON protocol v1 | Simple ordered scene commands and acks; no frame-by-frame streaming. |
| Physics kernels | TypeScript analytic expressions / precomputed geometry; Web Workers for heavier numeric kernels; WASM only if needed | No blocking on render thread. |
| Labels/math | Cached fonts + build-time/precompiled math label sprites / textures | No synchronous LaTeX compilation during a lesson. |
| Assets | Versioned local scene recipes, optional compressed GLB/KTX2 in future | Cache-first; content-addressed, permitted assets only. |
| Testing | Vitest, Playwright, Pytest, hypothesis/property tests, browser XR emulator, physical headset profiler | Both graph and visual correctness, and realistic XR performance. |
| Observability | OpenTelemetry-compatible spans or lightweight structured timing events + simple benchmark dashboard | Per-turn perceived latency and per-frame render cost. |

**Architecture decision:** Do not build Unity and WebXR in parallel. IWSDK is the default because it shares Three.js rendering between desktop and Quest. Retain platform-neutral scene DSL so Unity/OpenXR is possible *later*.

**Dependency rule:** Before generating lockfiles, use current official IWSDK instructions. The package may require a `three` alias to the SDK's supported runtime. Do not mix arbitrary Three.js revisions with IWSDK. No version is hardcoded here to avoid stale installation guidance.

Official references:
- https://developers.meta.com/vr/documentation/iwsdk/guides/overview/
- https://developers.meta.com/vr/documentation/web/webxr-perf-workflow/
- https://developers.openai.com/api/docs/guides/realtime
- https://developers.openai.com/api/docs/guides/voice-webrtc
- https://developers.openai.com/api/docs/guides/voice-server-controls
- https://developer.mozilla.org/en-US/docs/Web/API/WebXR_Device_API/Lifecycle

---

## 3. END-TO-END ARCHITECTURE AND CRITICAL PATH

```text
USER VOICE  ───────── WebRTC audio ────────> REAL-TIME VOICE PROVIDER
    │                                               │
    │                          transcripts/events / tool calls
    │                                               ▼
    │                              Python Tutor Orchestrator
    │                              ├─ Graph Snapshot Cache
    │                              ├─ Dijkstra / bridge planner
    │                              ├─ Recipe selection
    │                              ├─ Structured scene planner
    │                              └─ Tool/asset authorization
    │                                               │
    │                                      WebSocket (scene v1)
    │                                               ▼
    ▼                              Browser Scene Command Queue
LOCAL INPUT ──> local intent ────────┬───────────────┤
    │                                │               ▼
    │                         Local recipe       Validated DSL
    │                         fast path               │
    └───────────> XR UI + renderer <─────────────────┘
                      ├─ frame-stable scene registry
                      ├─ local interactive parameter bindings
                      ├─ cached textures/geometry/fonts
                      ├─ GPU shaders / instancing
                      └─ WebXR device output

                    USER UNDERSTANDING / FEEDBACK
                                │
                                ▼
                          Assessment API
                                │
                                ▼
                         SQLite event history
```

### 3.1 Separate real-time lanes

**Lane A, frame-critical (local):** headset pose, input, parametric animation, hit-testing, drawing. Do not `await` inside frame callbacks. Scene parameter updates should be constant-time or bounded incremental work.

**Lane B, interaction-critical (local):** visual recipe lookup, parameter binding, UI feedback, cancellation, cached scene activation. Provide immediate acknowledgement for local interactions even when backend is unavailable.

**Lane C, speech-critical (WebRTC):** microphone uplink and playable speech downlink. Do not proxy raw audio through Python unless provider/network architecture requires it. Reconnection and mute controls are independent of scene WS.

**Lane D, reasoning and graph (server):** LLM tool calls, Dijkstra, memory context packaging, lesson choice, structured scene plan. Background tasks may prefetch likely next recipes and approved assets, but must not mutate visible scene without validated commitment.

**Lane E, slow retrieval/asset tasks:** curated asset lookup, GLB download, occasional heavier simulation. Run out of the critical path; load placeholders or procedural analogues immediately.

### 3.2 Concurrency/cancellation invariants

- Every user turn receives `turn_id`, `turn_epoch`, `utterance_id` and an optional `lesson_id`.
- Scene events have a monotonically increasing per-scene `seq` plus stable object IDs.
- On interruption, increment `turn_epoch`; reject late events from older epochs and cancel cancellable backend tasks.
- Already visible objects remain until explicit `clear` or replacement. Never blank the scene while planning.
- Graph results are snapshots with `graph_version` and `formula_version`; a late old result cannot overwrite a newer lesson plan.
- Coalesce fast repeated slider updates locally. Send only the final/debounced parameter to the backend if its value must affect instruction or history.
- Network reconnect requests the latest compact scene snapshot, not an unbounded replay of old patches.
- Route each action to a single owner. Server handles DB writes/tool calls, client handles rendering/local manipulation. Avoid duplicate voice tool execution on client and sideband.

---

## 4. QUANTITATIVE PERFORMANCE BUDGET AND SLOs

**Reference device:** Choose and record a physical Quest 3 headset, browser version, refresh rate, power/thermal state, Wi‑Fi, and model deployment region. Quest 3S can be a second comparison target. Do not infer physical headset performance from desktop emulator.

### 4.1 Measurable targets (MVP acceptance)

| Metric | Target | How to measure |
| --- | --- | --- |
| XR steady framerate | ≥72 Hz under normal lesson load; 90 Hz stretch target where supported | Headset performance HUD, browser tracing, ≥10-minute sustained workload |
| Frame deadline at 72 Hz | ≤13.9 ms p99 under bounded test workload | App CPU + GPU timings / real frame interval |
| Frame deadline at 90 Hz | ≤11.1 ms p99 if targeting 90 Hz | Same; check thermal throttling |
| Frame-thread scene patch work | ≤2 ms p95 per render frame, with work slicing | Performance marks; queue backlog metrics |
| Local cached interaction → visible change | ≤50 ms p95 | Input timestamp to next changed frame |
| WS scene command receipt → visible first frame | ≤100 ms p95 for cached primitive | Timestamp at receive and first displayed frame |
| Graph shortest-path calculation | ≤30 ms p95 on 10k-node/50k-edge warm in-memory synthetic graph | Server benchmark, excluding LLM and network |
| Graph-query full latency | ≤100 ms p95 locally for indexed read/snapshot lookup | Query + normalization + route, excluding network |
| End-of-turn → first audible AI output | ≤1.2 s p50, ≤2.5 s p95 in target network configuration | Client audio playhead instrumentation; network/provider dependent |
| End-of-turn → first relevant visible response | ≤300 ms p50 for cached supported intents | Local intent detection and recipe activation; not dependent on LLM |
| Lesson plan generation | ≤2 s p50 for common recipes, optional later refinements | Structured-output model timing; not a gate to first visuals |
| Normal scene patch | ≤4 KiB typical, ≤16 KiB hard limit unless explicitly approved | Schema validation and transport metering |
| Scene health | no unhandled errors, no stale turn patches, no unbounded asset growth | Soak tests / memory snapshots |

Numbers here are **proposed team objectives**. Instrument first, then refine based on actual hardware. Response time depends on model, geography and connectivity; don't fake compliance by omitting slow attempts.

### 4.2 GPU and CPU allocation

At 72 Hz, total frame budget is about **13.9 ms**; at 90 Hz, **11.1 ms**. For a 90 Hz target, aim for ≲3 ms main-thread app work and ≲6 ms GPU rendering in benchmark scenes, leaving margin for runtime scheduling and device overhead. Report actual bottlenecks rather than treating sub-budgets as universal truths.

Baseline guardrails until profiling says otherwise:
- Favor fewer than ~50–100 draw calls per simple lesson, not thousands; record real draw calls.
- Start with ≤30 visible teaching objects, merging/instancing repeated elements.
- Use at most a few simultaneous transparent full-screen layers. Limit overdraw.
- Prefer unlit materials, lines, instancing, sprite labels and lightweight custom shaders.
- Preallocate/reuse typed arrays and GPU resources; avoid material/mesh creation each frame.
- Update uniforms for continuous time and user sliders; never rebuild geometry per frame where shader deformation works.
- Use low-cost approximate visuals first, optionally replace after precompute succeeds.
- Cap scene command intake per render frame; process queued structural changes across frames.
- Load compressed textures in offline asset pipeline; avoid huge base64 scene messages.
- Use adaptive quality for GPU pressure: lower curve sample count, disable secondary adornments, reduce framebuffer resolution only if necessary, and notify developer HUD.

### 4.3 Instrumentation

Collect a turn trace with spans:

`mic_activity → vad_end → voice_request → first_audio_played → graph_snapshot → graph_path → recipe_selected → first_scene_event_sent → event_received → first_visual_frame → first_beat_synced`.

For each client: render CPU frame time, estimated/available GPU frame time, dropped frame count, active draw calls, geometry count, texture bytes estimate, heap, WS queue depth, stale patch discards, input-to-photon proxy. For backend: model start/first event/done, graph query/path latency, validation failures, event size, cache hit %, WS retransmission/resync, SQLite transaction latency.

Never claim word-accurate audio synchronization from server event timestamps alone; prefer playback-confirmed cues on the client. Where provider gives only coarse timing, synchronize animation at semantic beat boundaries.

---

## 5. KNOWLEDGE GRAPH SPECIFICATION: DO NOT CHANGE THE MATHEMATICS

### 5.1 Graphs

Let concept IDs be global and stable. God Brain:

\[
G_g=(V_g,E_g,s),\quad s:E_g\to[1,10].
\]

For user \(h\):

\[
G_h=(V_h,E_h,u_h,f_h),\quad V_h\subseteq V_g,\quad E_h\subseteq E_g,
\]
\[
u_h:V_h\to[1,10],\qquad f_h:E_h\to[1,10].
\]

All graph edges are undirected; canonical ordered `(min_id,max_id)` persistence; symmetric retrieval. Node understanding and human edge familiarity are user scores, and `s` measures God Brain inherent topic similarity. `f_h` is **stored but not included in baseline Dijkstra weights**.

### 5.2 Exact baseline traversal cost

\[
\boxed{w_h(a,b)=\frac{1}{s(a,b)\,[u_h(a)+u_h(b)]+\varepsilon}},\qquad \varepsilon>0.
\]

Use configurable `epsilon=1e-6` initially, `formula_id=human_dijkstra_v1`. Scores stay bounded so costs are positive. For any valid path \(P=(v_0,...,v_k)\),

\[
C_h(P)=\sum_{i=0}^{k-1}w_h(v_i,v_{i+1}),\qquad
P^*\in\arg\min_{P:a\leadsto b}C_h(P).
\]

Use NetworkX multi-source Dijkstra over cached weights or on-demand weight callback, with deterministic tie-breaking by canonical node ID if minimum-cost ties arise. The shortest path **need not be unique**. Stop and return a typed `UNREACHABLE` when no path exists. Do not run LLM inside the edge-weight callback.

Important caveat: the baseline objective minimizes reciprocal weights; it is a **heuristic for teaching convenience**, not an empirically proven optimal learning path. Evaluate it later with user comprehension and time-to-understanding measurements.

### 5.3 Learning an unknown target / mini-God graph

Dijkstra on the Human Brain alone cannot reach an unknown concept absent from \(V_h\). Therefore define a distinct **candidate planning graph**:

1. Search a bounded relevant subgraph of God Brain around current known concepts and target.
2. Add proposed intermediate concepts and God edges as **unverified candidates**, tagged with provenance and confidence.
3. Estimate learning costs for unknown nodes with a **separate, versioned candidate-cost policy**. Do not mislabel missing human scores as observed understanding.
4. Compare existing-knowledge bridge path against starting a new topic from scratch using explicit, configurable `creation_cost` for proposed nodes.
5. Return a `LearningPlan` with route, reasoning, observed vs estimated fields, and selected visual recipes. Obtain user feedback before promoting candidate human edges or assessments.

**MVP:** ship exact Human Brain shortest path and a simple fallback when target absent: choose one authored/curated bridge concept or a scratch-learning recipe. Implement fully optimized mini-God candidate planning *after* the three reference lessons function; otherwise graph generation becomes an unbounded inference bottleneck.

### 5.4 Persistence and history

SQLite schema with migrations and indexes:
- `concept(id, canonical_name, aliases_json, description, created_at, updated_at)`.
- `god_edge(a_id,b_id, similarity, provenance, confidence, updated_at)`.
- `human_concept(user_id,concept_id,current_skill,updated_at)`.
- `human_edge(user_id,a_id,b_id,current_familiarity,updated_at)`.
- `assessment_event(id,user_id,entity_type,a_id,b_id_nullable,score,source,recorded_at,turn_id)`.
- `lesson_event(id,user_id,lesson_id,turn_id,type,payload_json,created_at)`.
- `visual_recipe(id,version,concept_id,recipe_key,verified,asset_manifest_json)`.
- `formula_registry(id,version,definition,epsilon,created_at)`.

Current skill derived from latest accepted assessment; history is append-only. Never silently reduce knowledge scores based on elapsed time. If an edge is absent from God Brain, flag and stage God-edge validation, not a random similarity value. Add graph-version cache invalidation for edits to incident nodes/edges.

### 5.5 Graph API (minimal)

- `GET /api/health`
- `POST /api/graph/concepts`
- `POST /api/graph/god-edges`
- `POST /api/users/{user_id}/concepts`
- `POST /api/users/{user_id}/edges`
- `POST /api/users/{user_id}/assessments`
- `POST /api/users/{user_id}/learning-path` accepting `{sources:[...], target, formula_id}`
- `GET /api/users/{user_id}/concepts/{concept_id}/history`
- `GET /api/recipes/{recipe_id}/manifest`
- `GET /api/lessons/{lesson_id}/snapshot`
- `WS /ws/scenes?lesson_id=...` with verified auth/session scope

Implement a user-scoped graph snapshot cache to avoid SQLite reads during every edge evaluation. Prevent arbitrary cross-user data access.

---

## 6. VISUAL SCENE DSL (CONTRACT FIRST)

**A scene is retained state, not a video.** The LLM sends minimal, declarative scene mutations to a trusted client runtime. The renderer owns interpolation, animation, geometry pooling, shaders and spatial layout.

### 6.1 Core primitive types (prioritized)

**P0:** `axes2d`, `grid`, `vector_arrow`, `angle_arc`, `line_curve`, `parametric_curve`, `text_label`, `equation_label`, `highlight`, `slider`, `group`, `camera_focus`.

**P1:** `wave_family`, `wave_packet`, `phasor`, `probability_density`, `potential_barrier`, `barrier_scattering`, `energy_level`, `complex_plane`.

**P2:** `axes3d`, `vector_field`, `surface`, `particle_cloud`, `linear_transform`, `bloch_sphere`, `lattice`, `field_lines`.

Each primitive must have immutable `id`, `type`, transform, bounded params, coordinate system, style token, `visible`, optional derived-data binding, and a dispose path. Persist canonical math units; renderer scale transforms are separate from physical units.

### 6.2 Allowed operations

- `scene.replace`: replace complete retained scene at lesson start/resync.
- `node.create`: create from a registered primitive or recipe.
- `node.patch`: update bounded properties, never arbitrary code.
- `node.remove`: remove idempotently.
- `animation.start`: play a whitelisted deterministic animation or shader time uniform.
- `animation.stop`: stop animation by id.
- `focus.set`: camera/attention target with motion comfort constraints.
- `label.show`: display precompiled or sanitized text/math label.
- `interaction.bind`: bind supported slider/gesture to whitelisted local parameter(s).
- `beat.mark`: attach visual transition to a semantic narration beat.
- `scene.snapshot_request` and `scene.ack` for recovery.

All op schemas must be versioned (`schema_version=1`) and modeled in **Pydantic** server-side and **Zod (or generated JSON Schema validator)** client-side. Keep Python/TS schemas synchronized through generated JSON Schema tests.

### 6.3 Protocol example

```json
{
  "schema_version": 1,
  "event_id": "evt_0031",
  "lesson_id": "lesson_demo",
  "turn_id": "turn_06",
  "turn_epoch": 4,
  "scene_id": "scene_wave_packet",
  "seq": 31,
  "base_revision": 12,
  "op": "node.create",
  "object_id": "packet_envelope",
  "payload": {
    "primitive": "wave_packet",
    "params": {
      "sigma0": 0.8,
      "k0": 4.0,
      "mass": 1.0,
      "hbar": 1.0,
      "t": 0.0,
      "domain": [-8.0, 8.0]
    },
    "style": "primary",
    "parent_id": "lesson_root"
  },
  "cue": {"mode": "immediate"}
}
```

**Validation:** require `object_id` stable; reject unknown primitives/fields, NaNs, infinities, invalid array lengths, extreme transforms, unbounded loops, untrusted asset URLs, malformed math, and oversize payloads. Server validates before publish; client validates again before apply. Do not use `eval`, `Function` or arbitrary model-generated GLSL.

### 6.4 Deterministic scene state machine

- Maintain `scene_revision`, ordered `seq`, active `turn_epoch`, `object_registry` and `animation_registry`.
- Apply patches on the next available animation frame with a small per-frame work budget; structural work may span frames.
- `base_revision` mismatch → request a snapshot. Out-of-order or duplicate packets → buffer briefly or discard based on `seq`; never apply a conflicting revision.
- Every create/remove/patch is idempotent by event ID. Preserve resources shared across scenes via reference counting.
- Acknowledgments report accepted/applied/rejected/seen-on-frame separately where needed; accepted does not mean displayed.

### 6.5 Scene recipe registry

Author **verified local recipe templates** for core concepts, keyed to graph concept IDs and explanation goals:

```text
recipes/
  vector_projection/v1.json
  dot_product/v1.json
  wave_packet_superposition/v1.json
  finite_potential_barrier/v1.json
```

The tutor first selects a recipe, then sets typed parameters and beat sequence. LLM creation of a completely new primitive type is a **development-time workflow requiring review**, not a runtime path.

---

## 7. LIVE SPEECH, TEACHING POLICY AND VISUAL SYNCHRONIZATION

### 7.1 Voice session control

- Browser obtains an ephemeral voice-session credential through the backend. Never ship long-lived provider keys to the client.
- Browser connects to the voice provider directly via WebRTC for audio. Backend receives appropriate sideband/tool events if supported; otherwise use an adapter with explicit event routing.
- Maintain a separate persistent WS for scene commands, snapshots, user interactions, and graph results.
- Voice provider adapter exposes: `on_partial_transcript`, `on_user_turn_committed`, `on_audio_started`, `on_audio_ended`, `on_interruption`, `emit_tool_result`, `speak`, `cancel` (actual capabilities/provider variations must be handled explicitly).
- Use input VAD/turn detection as supported, but always permit explicit push-to-talk fallback.

### 7.2 Two-stage response, no full-plan gating

**Stage 0, local cached intent (first useful visual):** On stable utterance evidence or committed user intent, show a cached recipe preview. If confidence is low, show a neutral whiteboard/axes, not a wrong technical claim. Do not run arbitrary LLM logic in frame loop. Avoid flickering from unstable partial transcripts by requiring a short stability interval or final turn for high-impact scene selection.

**Stage 1, LLM-selected explanation (live refinement):** Gather relevant graph snapshot in parallel with scene prep. Let the tutoring model emit a few semantic beats at a time (goal + recipe + params + explanation sentences). Validate and stream one atomic beat's visual changes; speak concurrently. Don't wait for the remaining beats.

**Stage 2, optional enrichment:** Prefetch additional math labels, 3D assets or simulation outputs; patch into scene only if still relevant to latest turn epoch. If not ready, the procedural visual remains correct and usable.

### 7.3 Beat synchronization protocol

Represent a teaching beat as `{beat_id, say_text, cue, scene_events[], expected_concept_ids[], allow_interrupt:true}`. Cue options:
- `immediate`: appear when validated (suitable for first axes and setup).
- `on_utterance_start`: display when client reports audio playout start for associated utterance.
- `on_beat`: client-triggered semantic boundary from provider or speech timeline, only if supported.
- `manual`: user gesture / slider.

**Do not rely on synthesized audio chunk arrival as proof it was played.** Audio buffering/network delays create skew. If precise timestamps are unavailable, favor narration structured in short sections, and show visual *just before or with* the utterance. Instrument visual/audio skew and adjust.

### 7.4 Barge-in

When user begins a new utterance mid-explanation:
1. Cancel the current output audio according to voice provider API.
2. Increment `turn_epoch` in coordinator and client.
3. Drop queued but uncommitted old-scene events; keep current scene intact.
4. Update intent, graph query and planner.
5. Patch focus or selected primitive rather than recreate the entire world.

Target interruption response (UI feedback) ≤100 ms p95 locally, excluding remote response generation.

### 7.5 Pedagogical policy

Input: `{user_id, goal_concept, known_concepts, graph_path, recent_user_utterance, previous_misconceptions, available_recipes}`. Output: ordered `TeachingBeat[]` with bridge concepts, difficulty choice, scene IDs, speech and comprehension check. The graph determines conceptual route, but the scene engine separately chooses appropriate visual modality. The model may quote graph scores and propose topics but **cannot silently assign new human understanding scores**.

---

## 8. MATH/PHYSICS ACCURACY AND GPU EXECUTION

### 8.1 Data flow for a math primitive

`typed params → validated physical domain → analytic expression or tested numerical kernel → GPU uniforms / precomputed typed arrays → draw`.

Do not turn untrusted model output into shader source at runtime. For common problems ship authored shaders and compiled pipelines. For generic curves, use a restricted expression grammar compiled in an approved way or evaluate in a worker with execution budget; a whitelist of analytic functions is preferable in MVP.

### 8.2 Verified reference physics modules

**Wave packet (free-particle Gaussian):** for real initial Gaussian width `sigma0`, central wavenumber `k0`, mass `m` and \(\hbar\), use an analytically verified solution and plot real/imaginary parts or density according to lesson intent. Verify width evolution:

\[
\sigma_x(t)=\sigma_0\sqrt{1+\left(\frac{\hbar t}{2m\sigma_0^2}\right)^2}.
\]

Do not conflate wavefunction amplitude, probability density, envelope, group velocity and phase velocity. Constrain `sigma0>0`, `m>0`, sane `k0`, `t`, and domain. Continuous evolution should update shader uniforms; don't recreate the curve every animation frame.

**Vector projection:** \(\operatorname{proj}_u v = (u\cdot v / (u\cdot u))u\), with a clear singular case at \(\|u\|=0\). Dot-product angle and projection lengths must agree numerically with the displayed equations.

**Finite barrier:** solve piecewise 1D stationary Schrödinger scattering with matching \(\psi\) and \(\psi'\) at interfaces and distinct \(E<V_0\) and \(E>V_0\) regimes. Test probability flux conservation \(R+T\approx1\) for a real, lossless potential. Do not represent probability density by incorrectly interfering amplitude components without labeling.

### 8.3 Physics verification

- Golden analytic unit tests for every core primitive (known values, continuity, asymptotic limits, dimensional consistency).
- Property tests across randomized valid parameters (nonnegative density, finite outputs, normalization/flux within tolerances).
- Screenshot/image comparisons of authored baseline scenes on desktop.
- Human-reviewed small reference sequences for physical correctness and explanatory coherence.
- If the model requests unsupported physics, offer a basic graph/axes/explanation and indicate that no verified animation is available. Never invent equations for attractive motion.

### 8.4 Graphics optimizations

- Keep scene graph stable; assign shader uniforms for `time`, slider parameters and highlights.
- Render repeated arrows/particles as instanced objects rather than thousands of draw calls.
- Precompute label/glyph atlases, place labels as sprites or spatial UI elements; batch wherever possible.
- Use camera-relative billboarding for legible text, avoid excessive depth conflicts and screen-filling transparent quads.
- Make 2D math explanations into panels anchored in 3D space; don't force 3D when 2D is clearer.
- Run large sampling, mesh generation and numerical integration in workers or batch while the lesson progresses; return transferable buffers. Main thread only commits bounded GPU updates.
- Asset registry caches geometries/materials by stable key; verify disposal after removal and scene switch.
- High frequency input controls update local uniforms immediately and *do not call the model per slider movement*.

---

## 9. SECURITY, RELIABILITY, USER PRIVACY

- Authenticate HTTP and WS endpoints; authorize `user_id` and `lesson_id` on every action. Do not trust IDs supplied by client without account context.
- Ephemeral credentials for realtime voice; long-lived API keys live only server-side.
- Validate all tool arguments and LLM output with strict allowlisted schemas; impose op count, param ranges, update frequency, scene size, token/compute and memory limits.
- Never fetch model-supplied arbitrary URLs from the client; maintain an allowlisted content-addressed asset manifest. Block SSRF at backend fetch layer as well.
- Redact transcripts and assessment data from production logs; opt-in retention policy; provide export/delete of personal records.
- Persist structural lesson events and assessment changes only after validation; idempotency keys avoid duplicate writes on reconnect.
- Model/tool timeout → use local recipe and voice fallback. WS outage → cached scene stays interactive, queue bounded and user shown reconnect status. Voice outage → allow text input and keep visuals interactive. Unsupported AR → fall back to VR or desktop.
- Every slow asynchronous operation has deadline, cancellation, stale-epoch protection, and a circuit breaker if a provider repeatedly fails.
- Telemetry must not include unredacted sensitive voice, transcripts or personal knowledge by default.

---

## 10. REPOSITORY STRUCTURE

```text
lucid/
  README.md
  IMPLEMENTATION_PLAN.md
  pyproject.toml
  package.json                  # workspace root if needed
  docs/
    PROGRESS.md                 # phase completion, commands, evidence
    ARCHITECTURE.md
    ADR/                       # architecture decision records
    METHODOLOGY.md              # graph formula and teaching model
    LATENCY_BUDGET.md
    BENCHMARKS.md
    SCENE_DSL.md
    PHYSICS_VALIDATION.md
  config/
    perf_targets.json
    model_policy.json
  backend/
    app/
      main.py
      api/
      graph/{models.py,store.py,paths.py,cache.py}
      learning/{planner.py,recipes.py,policy.py}
      scene/{schema.py,validator.py,emitter.py}
      voice/{adapter.py,session.py,tools.py}
      db/{models.py,migrations/}
      telemetry/
    tests/{unit/,integration/,performance/}
  frontend/
    src/
      main.ts
      xr/{world.ts,session.ts,inputs.ts}
      runtime/{sceneStore.ts,applyEvent.ts,resourcePool.ts,commandQueue.ts}
      protocols/{scene.schema.json,scene.ts}
      primitives/{axes2d.ts,vectorArrow.ts,wavePacket.ts,potentialBarrier.ts}
      recipes/
      physics/{wavePacket.ts,projection.ts,barrier.ts}
      voice/{provider.ts,session.ts}
      net/{sceneSocket.ts,reconnect.ts}
      ui/{whiteboard.ts,assessment.ts,metricsHud.ts}
      workers/{curveSampler.worker.ts}
      tests/{unit/,integration/,visual/,perf/}
    public/assets/
    iwsdk.config.json
  shared/
    schemas/scene.v1.schema.json
    schemas/learning-plan.v1.schema.json
    examples/scene-wave-packet.json
  scripts/
    benchmark_graph.py
    benchmark_scene.ts
    validate_schemas.py
  .github/workflows/ci.yml
```

Agent may adjust the scaffold to align with current IWSDK project conventions but **must keep the domain boundaries and schema versioning**.

---

## 11. PHASED IMPLEMENTATION BACKLOG WITH ACCEPTANCE GATES

### PHASE 0: Repository, documentation, baseline instrumentation

**Implement:** IWSDK starter, Python backend, shared schema directory, local HTTPS dev path, desktop emulator, health check, lint/test commands, demo shell scene, README, `PROGRESS.md`, metrics HUD.

**Gate:** fresh clone boots both services with documented commands; desktop sees interactive 3D scene; backend `/api/health` responds; no credentials committed; CI builds TypeScript and runs Python tests. Capture first baseline frame timing. Confirm one physical target headset and exact runtime setup in docs.

### PHASE 1: Retained scene runtime and safe DSL (highest priority)

**Implement:** scene v1 schema, `SceneStore`, object registry, `node.create/patch/remove`, revision/sequence protection, resource pool, bounded apply queue, `scene.replace`, snapshot recovery; basic `axes2d`, `vector_arrow`, `line_curve`, `text_label`, `slider`.

**Gate:** local scripted 100-event lesson renders with no duplicates; invalid commands rejected safely; 500 consecutive scene changes do not leak observable GPU resources; disconnected backend does not affect FPS. UI slider updates projection visually without WS.

### PHASE 2: God Brain / Human Brain graph + history

**Implement:** DB migrations; God/Human graph models, same concept IDs, score bounds, undirected edge normalization, all APIs, exact formula, Dijkstra/multi-source, event-sourced assessments, cache invalidation, `METHODOLOGY.md` and formula registry.

**Gate:** deterministic hand-calculated path tests; generated graph comparison against NetworkX; graph warm-path benchmark and multi-user isolation pass; assessment at time `t2` never deletes score at `t1`; human familiarity stored but excluded from baseline cost.

### PHASE 3: Scene transport and lesson scripts

**Implement:** authenticated WS, `turn_epoch` & seq, scene snapshots, idempotent patch delivery, `LessonPlan`/`TeachingBeat` models, recipe selector, deterministic authored vector projection recipe, events driving frontend.

**Gate:** typed text query “Explain dot product” produces a suitable multi-beat animation; deliberate reordered/duplicated packets don't corrupt scene; reconnect yields same final scene as uninterrupted stream. First cached scene <100 ms after receipt p95 in benchmark.

### PHASE 4: Live voice and cancellation

**Implement:** provider adapter, browser WebRTC session with ephemeral credential, transcript/turn events, speech tool bridge, scene events synchronized at semantic beats, interrupt/barging, voice degradation to text.

**Gate:** live speech query triggers animation and spoken lesson; interrupt and replan does not display old beats; provider outage does not freeze renderer; audio/scene timing captured. Profile first audible response and initial visible response; tune toward §4 targets.

### PHASE 5: Wave packet and barrier + verification

**Implement:** 2 high-value physics recipes, shader uniforms, worker sampling fallback, math sprites/labels, timeline play/pause/scrub, sliders, physics golden tests, visual review set.

**Gate:** wave packet width follows analytic formula; vector projection math exact within tolerance; finite barrier flux verified; sliders maintain frame budget; no arbitrary formula executed from model. End-to-end all three reference lessons are teachable.

### PHASE 6: Personalized teaching loop

**Implement:** context-efficient graph query (local neighborhood/path summary not full graph in prompt), bridge/recipe selection, comprehension prompt (1–10), assessment persistence, user-specific explanation variation, memory/snapshot invalidation; unknown-topic fallback.

**Gate:** two seeded users with different skill profiles receive measurably different bridge concept sequences for same goal; both receive the same correct physics; each user rating updates only own graph; future query sees new score and recomputed weight. Unknown target does not crash or fabricate human understanding.

### PHASE 7: Physical headset optimization and resilience

**Implement:** Quest immersive VR and passthrough AR detection, world-anchored whiteboard, gaze/controller input, hand input only where supported; warm caches, adaptive LOD, stress tests, throttling, load shedding, soak/thermal profiling.

**Gate:** ≥72 FPS target under specified 10-minute reference workload, p99 frame timing near/below corresponding budget, no crashes or interaction stalls. Repeat 20-minute continuous talk/interrupt/slider test. If 90 Hz is selected, document results separately. Validate fallback modes. Publish `BENCHMARKS.md` with both failures and successes.

### PHASE 8: Advanced mini-God expansion (after MVP)

**Implement:** candidate God subgraph proposal generator, provenance/trust/review, creation costs, competing “learn from scratch” option, versioned candidate-cost model, optional node/edge import from Obsidian, offline evaluation of alternative traversal metrics.

**Gate:** known vs candidate costs are never conflated; unknown topics can be planned with explicit assumptions; no prompt-time LLM work in per-edge Dijkstra callback; experiments reproducible with formula versions.

---

## 12. TEST MATRIX AND FAILURE INJECTION

**Graph tests:** same directed-order inputs produce one canonical undirected edge; correct scores/bounds; zero/infinite/NaN rejected; exact cost for `s=8`, `u_a=9`, `u_b=3`, `epsilon=1e-6` equals `1/(96+1e-6)`; connected/disconnected/tied paths; cache updates after skill change; assessment history stable; user segregation; 10k/50k benchmark.

**DSL tests:** unknown type/field rejection; stale epoch; duplicate `event_id`; patch before create; malformed base revision; huge request; invalid numerics; creating/removing same ID; idempotent resync; device feature lack; geometry/memory cleanup.

**Teaching tests:** text-to-visual with and without graph; one known bridge/one unknown; interruption during cue; stale lesson; invalid LLM response retried once then deterministic fallback; no visual when concept unsupported except safe whiteboard.

**Voice tests:** dropped WebRTC; dropped WS; latency spikes; muted mic; clipped speech; interrupted narration; provider timeout; browser permission denied; multiple overlapping tools; reconnection with old queued outputs.

**XR stress:** repeat 1k slider adjustments, 500 object patches, scene replacements, long playbacks, loss/recovery of wifi, headset thermal warmup, high-dpi rendering, foreground/background session changes. Measure frame times and memory throughout.

**Safety tests:** prompt injection requesting executable shader/code, arbitrary URL, cross-user graph read, negative physical mass, extreme mesh size, malicious text label, huge expression, replay of old session event. All must be rejected or converted into safe output.

---

## 13. BUILD/TEST COMMANDS TO PROVIDE

The agent must define working commands analogous to these; substitute actual package script names as appropriate and document them:

```bash
# Backend
uv sync
uv run pytest backend/tests -q
uv run uvicorn backend.app.main:app --reload --port 8000

# Frontend
npm install
npm run dev
npm run typecheck
npm run test
npm run build

# Performance / integration
uv run python scripts/benchmark_graph.py
npm run test:e2e
npm run benchmark:scene
```

The generated project's scripts must run from the documented working directory (monorepo root vs subpackage). The agent should not leave these as nonfunctional placeholders. Add scripts for schema compatibility checks and deterministic tests, then wire them to CI.

---

## 14. REPORTING FORMAT REQUIRED FROM CODING AGENT

At the end of each phase, append to `docs/PROGRESS.md`:

```markdown
## Phase N — <name>
Status: PASS / PARTIAL / FAIL
Commit: <hash or uncommitted>
Files implemented: ...
Commands run: ...
Test results: ...
Measured perf (device / conditions / p50 / p95 / p99): ...
Known defects: ...
Next exact implementation tasks: ...
```

When blocked by missing external API credentials or physical hardware, create a working local fake provider/emulator and continue to complete all feasible tests. Clearly label hardware-dependent measurements as **not yet measured**, not passed.

Maintain `docs/METHODOLOGY.md` and `config/model_policy.json` with every cost formula/version, its interpretation and known caveats; ensure changes are reviewable and reproducible. Avoid destructive schema edits or unrelated refactors during phase implementation.

---

## 15. DEFINITION OF DONE

The MVP is complete **only when all are true**:

- A user can start a text or voice teaching session on desktop, and on a chosen tested Quest device.
- Three reference lessons produce correct, inspectable, manipulable visuals with synchronized narration.
- Visuals begin from safe cached recipes before full model reasoning when supported, and continue running during service stalls.
- User can interrupt mid-explanation and see the correct new visual plan, never stale old commands.
- God Brain/Human Brain scores, assessments, exact reciprocal Dijkstra costs and knowledge paths work, persist and are versioned.
- An understanding score is written with timestamp and visible in subsequent graph queries.
- Headset render budget and user-perceived latency have real p50/p95/p99 measurements; acceptance gaps are documented and tuned, not concealed.
- Schema validation, reconnection, user isolation, reproducible tests and physics truth checks all pass.
- The repository contains runnable setup, tests, examples, methodology, performance logs and phase progress.

**Do not begin with text-to-video. Start with a scene DSL and verified procedural teaching components. The LLM chooses the explanation; deterministic graphics make it true and fast.**
