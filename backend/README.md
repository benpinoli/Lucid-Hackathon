# LUCID scene and tutor backend

## Desktop / WebXR companion

The connected frontend is in [`frontend/`](../frontend/README.md). For local demo
development, `$env:PYTHONPATH='src;.'` followed by
`backend/.venv/Scripts/python.exe -m backend.tools.dev` seeds the demo, configures
this process, prints the private browser token, and starts one backend worker.
In another terminal run `cd frontend`, `npm ci`, `npm run dev`, then open
`http://localhost:5173`. Full desktop/headset setup, TLS, testing, architecture,
and troubleshooting are documented in the frontend README.

## Backend setup and protocol

Run commands from the repository root in PowerShell. Requires Python 3.12+.
The memory package remains owned by the memory agent; this service imports its public API
using `PYTHONPATH=src;.`. It does not maintain a second graph implementation or migration tree.

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e .
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.lock
$env:PYTHONPATH='src;.'
backend/.venv/Scripts/python.exe -m backend.tools.demo
# Copy the two printed environment assignments (database URL and private credential).
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --ws-max-size 16384
```

The demo creates `backend/demo.db` once, with user UUID `00000000-0000-0000-0000-000000000064`,
Vectors `00000000-0000-0000-0000-000000000001` and Dot product `00000000-0000-0000-0000-000000000002`.
Existing databases are left intact. `/docs` contains the interactive API specification.
Health is public; every personal endpoint requires `Authorization: Bearer TOKEN`.
`LUCID_TOKENS_JSON` maps separate random credentials to provisioned human UUIDs.
Startup fails without credentials. Never commit tokens. Configure real account authentication before hosting publicly.

```powershell
$token = (ConvertFrom-Json $env:LUCID_TOKENS_JSON).PSObject.Properties.Name
$headers = @{Authorization="Bearer $token"}
$lesson = Invoke-RestMethod http://127.0.0.1:8000/api/lessons -Method Post -Headers $headers
$body = @{text='Explain dot product';expected_epoch=0} | ConvertTo-Json
Invoke-RestMethod "http://127.0.0.1:8000/api/lessons/$($lesson.lesson_id)/turns" -Method Post -Headers $headers -ContentType application/json -Body $body
```

For the browser, connect `/ws/scenes?lesson_id=ID` using the same server, then send
`{"token":"TOKEN"}` within five seconds. Authentication is in the first message to avoid URL credential logs.
Origin must match `LUCID_ALLOWED_ORIGINS` (comma-separated exact origins, default localhost ports 5173 over HTTP/HTTPS).
First response is `scene.snapshot`. Send
`{"type":"turn","text":"Explain dot product","expected_epoch":0}`.
The result includes an explanation and validated `scene.replace` event. Recipes in
`backend/app/learning.py` are builders over typed parameters (vectors; a weighted graph with
source and target; a cubic-or-lower polynomial on an interval). They compute every number,
label and sentence and reject parameters outside the view or scene budget.

**Conversational tutor.** `backend/app/tutor.py` runs one session per lesson. Clients send
`{"type": "tutor", "action": "goal|answer|confused|question|continue|explore", "text": ...,
"expected_epoch": n}` over the scene WebSocket (or `POST /api/lessons/{id}/tutor`); every reply is
a normal `lesson.turn` whose plan carries `tutor: {mode, awaiting_answer, goal}`.

- *Goal*: resolved to a concept (curriculum alias, exact name, Claude resolver), or created.
- *Starting check*: a frontier search from the goal. Each spoken answer is graded and recorded
  (`voice_diagnostic`: 7/4/2/1 for correct/partly/wrong/unsure); a correct answer stops the search
  along that branch, so learners who know more answer fewer questions (at most 8).
- *Silent route*: the engine's baseline Dijkstra picks the next concept; the tutor only ever says
  "Next, let's look at X". Routes, costs and scores are never spoken or shown.
- *Teaching*: verified recipe, else an authored diagram lesson (`diagrams.AUTHORED`, all 12
  non-recipe curriculum topics), else a Claude diagram lesson. Every explanation has a diagram.
  Each ends with a spoken check question, graded and recorded (`voice_check`: 8/5/3/2), which
  also rates the edge just crossed. Scores change only through graded answers.
- *Confusion*: a deeper Claude explanation with a new diagram (or the lesson again, slower,
  without Claude). If Claude names a missing prerequisite the learner doesn't know, the tutor
  detours to it and resumes afterwards. Asking for help never counts against the learner;
  three wrong answers move on instead of looping.
- *New concepts*: goals or prerequisites the graph lacks are created by Claude and written to the
  shared God Brain through the engine's proposal API (submitted and accepted, with `claude:<model>`
  provenance), linked only to concepts they directly build on.

**Diagram language.** `backend/app/diagrams.py`: formulas (parametric curves), arrows, points,
labels and node-edge graphs, plus up to three sliders binding single-letter parameters. LUCID
validates every expression (`expressions.py`, an allowlisted grammar with no eval, mirrored in
`frontend/src/expressions.ts` and checked against `shared/fixtures/expressions.json`), checks the
default state is in view and within the 30-object budget, then emits `expr_curve`, `expr_arrow`
and `expr_point` primitives. Formulas stay symbolic in the scene, so sliders re-plot on the client
with no network call. Claude-written lessons get one corrective retry on a diagram error and are
flagged `generated`; the renderer shows a small "AI-generated" marker.

**Learning goals.** `backend/app/goals.py` holds an authored 16-topic curriculum (vectors,
graph algorithms, calculus) with descriptions, one quiz question per topic and "builds on" links
with similarity. Startup syncs it into the God Brain: additive and idempotent, reusing concepts
by stable ID or exact name (the demo's Vectors and Dot product keep their IDs); no score is edited.
The flow, all user-scoped and Bearer-authenticated:

1. `POST /api/users/{id}/goals {"text": "I want to learn the mean value theorem"}` resolves the
   goal (aliases, then Claude when configured; unknown topics return 404 with suggestions) and
   returns quiz questions for the nearest curriculum concepts *not already in the learner's
   Human Brain*, foundations first. Answer keys never leave the server.
2. `POST .../goals/{target}/diagnostic` records each answer as an understanding assessment
   (`diagnostic_quiz`: correct 7, wrong 2, not sure 1, confidence 0.6) in one transaction, links
   members along God edges (initial familiarity = min endpoint understanding, method
   `derived_min_understanding`; familiarity is not in the cost) and returns the route.
3. The route is the engine's baseline Dijkstra (`1 / (s * (u_a + u_b) + eps)`, unchanged) over the
   personal graph from concepts with understanding >= 7 to the target; with none, from the
   cluster's foundation topics. `GET .../goals/{target}/route` recomputes it from current scores.
4. A WS turn with `route_to` shows the 3D route map; with `concept` it teaches that step (the
   verified recipe where one exists, otherwise an authored concept card).
5. `POST /api/users/{id}/progress` records the lesson self-rating (`lesson_self_report`), adds the
   concept if new, and rates the edge just crossed. Free-text lessons that match a topic carry its
   `concept_id`, so exploring also grows the graph. Writes take `expected_revision` and an
   idempotency key (409 on stale revision).

Curriculum links must mean "directly builds on": the baseline cost makes a weak link cheap once
one end is well understood, so a loose shortcut would let routes skip prerequisites. A test
simulates mastery on every advanced topic and asserts routes climb level by level.

**Lesson planner.** With `ANTHROPIC_API_KEY` set, `backend/app/planner.py` asks Claude
(`LUCID_PLANNER_MODEL`, default `claude-opus-5-5`, low effort, structured output) to pick a
recipe and parameters for the learner's request, e.g. their own graph or function. Claude never
writes coordinates or narration. Invalid parameters get one corrective retry; a second failure,
refusal, timeout (12 s per call) or outage falls back to the keyword router (dot product/
projection/vector, Dijkstra/shortest path, mean value theorem/MVT, else whiteboard). Requests
opt into server-side refusal fallbacks (`fallbacks: "default"`). Planning runs outside the
service lock and the epoch is rechecked before commit. `plan.planned_by` reports `claude` or
`authored`; health reports `planner`. Learner text is never logged.
`{"type":"interrupt","expected_epoch":1}` increments the epoch while keeping objects.
`{"type":"scene.snapshot_request"}` retrieves compact state. Send `scene.ack` with a sequence
to confirm receipt; this does not claim a frame was displayed. Ping with snapshot requests within 60 seconds.

HTTP turns return events to their caller. WS turns return events to that socket. Other sockets
must request a snapshot; this initial transport does not broadcast. There is no arbitrary client scene-write endpoint.
All scene commands are server-authored. No provider or inference call is made.
For graph-aware teaching, add explicit stable `sources` and `target` IDs to a turn; scores are never invented.
Use the user-scoped learning-path, assessments and history endpoints in `/docs`.
Assessments require current human revision and an idempotency key. A stale revision returns 409;
refresh the revision before retrying with the same key. History is owned and persisted by the graph engine.

Validation:

```powershell
$env:PYTHONPATH='src;.'
backend/.venv/Scripts/python.exe -m pytest -c backend/pytest.ini backend/tests -q
backend/.venv/Scripts/python.exe -m backend.tools.schemas --check
backend/.venv/Scripts/python.exe -m backend.tools.benchmark
```

Use one uvicorn worker: lessons, cancellation epochs and the bounded snapshot cache are in-process.
Scenes survive socket loss but expire on process restart; graph history remains durable.
There is a 128-lesson cap; `DELETE /api/lessons/ID` releases a slot.
WS intake is limited to 20 commands per second, 16 KiB per message, with a 60-second idle deadline.
HTTP writes are capped at 16 KiB. Terminate TLS for remote access; future headset clients require HTTPS/WSS.
Only trusted renderer code may interpret local slider bindings. Slider changes must stay local and never require model calls.

This backend does not establish rendering, audio playback, GPU cleanup or physical headset acceptance.
Voice: with `OPENAI_API_KEY` set, `POST /api/voice/sessions` (Bearer auth) returns a short-lived
OpenAI Realtime credential and WebRTC SDP URL; otherwise it returns 503 and health reports `text_only`; wave-packet and barrier recipes remain unimplemented. See [progress](../docs/PROGRESS.md).
