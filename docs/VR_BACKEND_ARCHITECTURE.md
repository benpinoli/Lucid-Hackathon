# LUCID backend integration boundary

The concurrent memory implementation owns `src/memory_graph`, root Python packaging,
`tests/`, migration history, methodology and graph benchmarks. LUCID owns `backend/`,
`shared/schemas/`, `docs/PROGRESS.md`, this document, `docs/VR_METHODOLOGY.md` and its separate CI workflow.
No graph tables or migration files are duplicated. No shared database was opened during development;
tests use temporary databases, and the optional demo uses `backend/demo.db`.

The repository had an unborn `main` branch and all implementation files were untracked.
Switching branches would carry those same files. Directory ownership is the isolation mechanism;
no root packaging, README, tests, lockfile or memory source files were edited by this implementation.

HTTP/WS authentication resolves a configured token to a provisioned human UUID. Client-provided
user IDs never establish identity. Lesson ownership is checked on every action. This is a local demo
authentication boundary; an account provider is future work. No microphone data is stored or logged.

Sync FastAPI routes run outside the event loop. WS graph/planning work uses a worker thread.
A coordinator lock protects in-process scene sequence/epoch state. Graph transactions remain owned
by MemoryGraph. No renderer, inference or physics computation runs in a headset frame callback here.
The backend returns declarative data only; scene validation uses Pydantic strict models.
See the official [FastAPI WS guidance](https://fastapi.tiangolo.com/advanced/websockets/)
and [Pydantic strict validation](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/).

Scene v1 currently supports five primitives and replace/create/patch/remove operations. A patch
contains a complete typed node; primitive type and ID cannot change. Server lesson emission uses
atomic replacement; semantic beats identify focus objects for the future renderer. Cues currently
support immediate/manual only. Audio-confirmed cues and incremental timed beat emission are pending.
Snapshots contain current sequence, revision, epoch and bounded nodes, not historical packet replay.
The renderer must drop older epochs, enforce base revision/sequence and request a snapshot on gaps.

The bounded LRU stores immutable user-scoped graph snapshots with database revision pair and active
formula configuration hash. Dijkstra operates on those snapshots, never queries SQL per edge.
Human and God score changes alter cache keys; assessment writes also clear cached entries.
There is one graph service/database per process and a strict eight-snapshot cap.

Limits: 16 KiB HTTP writes/WS messages, 30 nodes, 128 active lessons, 256 remembered event IDs,
20 WS commands per second, five-second authentication and 60-second idle timeout.
Use one worker, loopback binding, and TLS before remote access. Restart loses in-memory scenes.
The backend never accepts model-generated URLs, code, shaders or arbitrary formula expressions.
