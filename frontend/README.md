# LUCID desktop / WebXR frontend

A TypeScript + Vite + Three.js client for the existing FastAPI scene service. Desktop
means a browser-based desktop experience; no Electron/native installer is included.
Both desktop and immersive VR share one renderer and the backend's versioned scene contract.

## Start locally (Windows PowerShell)

Requires Node **22.12+** and Python **3.12+**. Run the backend with one worker.
From the repository root, terminal 1:

```powershell
# Only if the backend environment is not installed yet:
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e .
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.lock

$env:PYTHONPATH='src;.'
backend/.venv/Scripts/python.exe -m backend.tools.dev
```

This seeds `backend/demo.db` if absent, sets database/credential environment variables
inside this process, prints a private token, and starts on `127.0.0.1:8000`.
Existing demo data is preserved. Each restart generates a new token. This command is
specifically for the demo database, not an existing production account/database.
For custom credentials/data, follow [backend setup](../backend/README.md) instead.

Terminal 2, from the repository root:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open **http://localhost:5173** (use `localhost`, which the backend allows by default).
Paste the printed token, select **Connect**, then **Explore concept**.
The default question produces the authored dot-product scene. Ask about Dijkstra's algorithm
or the mean value theorem for the other two authored lessons (these have no local sliders). Drag to orbit,
scroll to zoom, select explanation beats to highlight objects, and adjust the angle
slider locally. **Reset view** restores the camera; a new turn resets local sliders.
**Interrupt** advances the server epoch and retains the objects. **Disconnect**
closes the socket and deletes the lesson to release capacity.

**Visual studio.** Dot product starts with an illustrated parallel-light shadow experiment.
Switch between **Shadow story** and **Vector model**. **Play animation** sweeps a selected
parameter smoothly; pause, change speed, drag the slider, or reset to the original example.
Playback runs locally. Equations use LaTeX via KaTeX below the drawing, and narration stays
outside the geometry. Generated lessons default to 2D unless depth is useful.

**One Realtime teacher.** Set `OPENAI_API_KEY` in the backend terminal before starting it.
This branch uses only **gpt-realtime-2.1** for speech, typed replies, lesson planning, diagram
code, hints, new concepts and grading. No Anthropic key or second model is used. Without an
OpenAI key the checked built-in examples still work locally.

Type a topic and choose **Start learning**, or choose **Start voice** and speak. Typed requests
open the same Realtime connection without microphone permission and request text output.
Voice enables the microphone and audio output; typed follow-ups then share that conversation.
The backend preloads brain context, learner notes and the current screen into the session.
Tools refresh this context, create concepts, compile diagram code, publish a step, advance
prepared steps, ask questions and save evidence-backed assessments. Scene code is the same
validated expression/diagram language used by the renderer; no arbitrary Python or JavaScript
is executed. Errors go back to the same model for repair.

Each lesson presents one small visual step, then listens. **Continue** requests the next step;
silence never advances a lesson. **I'm confused**, **Hint**, **Review**, and **Go deeper** go to
this same agent. Lessons keep the Jay Cummings style guidance and problem-first approach.
Only actual graded answers update mastery. Skipping a question and viewing a lesson do not.
The optional adaptive-route checkbox asks the teacher to bridge through familiar prerequisites.

**Turn-taking.** `LUCID_VOICE_EAGERNESS` defaults to `low` and
`LUCID_VOICE_NOISE_REDUCTION` to `near_field` (`far_field`/`off` also supported).
`LUCID_VOICE_NAME` defaults to `marin`. The model is fixed to `gpt-realtime-2.1` on this branch.
Use **Interrupt** or the VR trigger to cut speech off; headphones are recommended for
**Interrupt by talking**. Canceled tools cannot overwrite a later scene. The private OpenAI
key remains on the backend; the browser receives a ten-minute credential over authenticated
HTTP and connects to OpenAI with WebRTC. A headset microphone requires HTTPS.

Keep both terminals running. Ctrl+C stops each service. Lessons expire when the backend
process stops, while graph data in the demo database persists. Reconnect creates a
new lesson; socket loss during a session automatically restores the existing one.

## Integration and ownership

- Vite proxies `/api` and `/ws` to `http://127.0.0.1:8000`; HTTP and WS use the page's
  origin. No CORS changes or browser-visible backend URL are necessary.
- The password input is cleared after connection. The token lives only in the client
  instance, never local/session storage, URLs, source, or `VITE_*` build variables.
- HTTP uses Bearer auth. WS authenticates in its first message. The proxy preserves
  Origin so the backend can enforce its exact allowlist.
- Create a lesson over HTTP, connect its WS, receive a snapshot, then send turns
  with `expected_epoch`. Scene receipt is acknowledged; this is not a displayed-frame ACK.
- JSON Schema validation uses `shared/schemas/scene.v1.schema.json` and
  `learning-plan.v1.schema.json` directly. Runtime checks also enforce slider bounds,
  unique IDs, object budgets, scope, epochs, sequence and revisions. A failed event
  leaves the previous state intact; a snapshot restores synchronization.
- The client allows one command at a time. Snapshot heartbeats every 20 seconds keep
  the server's 60-second idle deadline satisfied. Handshake/response deadlines and
  bounded exponential reconnects handle broken connections. Rejected credentials or
  exhausted retries require an explicit new connection. Ambiguous turns are never replayed.
- Snapshots carry scene state, not teaching plans. A full reload/new session must send
  a new turn to get text. The backend does not broadcast to other sockets.
- Unknown primitives, executable content and remote assets are rejected; 3D models load only
  by catalog id from the backend's `/api/models/{id}.glb` (Draco decoder served locally at
  `/draco/` from three's own files), parsed once and cloned per scene object. Text is
  rendered with `textContent` or canvas. Only the authored vector recipe binds local
  `projection.angle`/`projection.length` values to vector geometry. There is no model call.
- Local changes are view-only, reset on a received changed snapshot/new turn, and do
  not update server state or learning scores. Teaching beats describe the original
  server geometry; this is explicitly indicated under the sliders.

The frontend currently implements the lesson/scene flow. Learning-path queries,
assessment submission, history, voice/audio, arbitrary scene editing and graph browsing
remain separate future UI work; their backend APIs are available in `/docs`.

## Headset / HTTPS setup

Desktop localhost works over HTTP. A headset visiting another computer's LAN address
needs a secure origin: serve **HTTPS**, use **WSS**, and install/trust the issuing
certificate on the headset. A self-signed certificate warning bypass alone is not a
reliable secure-context setup. Never disable browser certificate validation.

1. On your development machine, obtain a trusted development certificate whose SANs
   include the exact LAN hostname or IP you will visit. Keep the key private. `.pem`
   files are ignored; keep other private-key formats outside the repo too.
2. In the frontend terminal, configure Vite before starting it:

   ```powershell
   Set-Location frontend
   Copy-Item .env.example .env.local
   # Edit .env.local, using your real paths and LAN address:
   # LUCID_FRONTEND_HOST=0.0.0.0
   # LUCID_TLS_CERT=C:/certificates/lucid.pem
   # LUCID_TLS_KEY=C:/certificates/lucid-key.pem
   npm run dev
   ```

3. In the backend terminal, before running the backend, explicitly allow the page origin:

   ```powershell
   $env:LUCID_ALLOWED_ORIGINS='https://localhost:5173,https://192.168.1.50:5173'
   $env:PYTHONPATH='src;.'
   backend/.venv/Scripts/python.exe -m backend.tools.dev
   ```

   Replace the example address. Origins have scheme, host and port, without a trailing
   slash/path. If using a custom hostname, also set Vite `server.allowedHosts` to that
   exact trusted hostname in `vite.config.ts`. Do not set it to `true`.
4. Allow port 5173 only on your trusted local network, connect the headset to that
   network, and visit the certificate's exact HTTPS address. The backend can stay on
   loopback because Vite handles both proxy routes.
5. Connect, load the lesson, then choose **Enter VR**. Controller rays can activate the
   world-space **− angle / + angle** buttons with a trigger. The lesson and current
   caption appear in the headset. Exit VR to enter a new question or select other beats.

XR uses Three.js `VRButton`, `renderer.xr.enabled`, and `setAnimationLoop`, following
the [official Three.js WebXR guide](https://threejs.org/manual/en/webxr-basics.html).
TLS and proxy configuration follow [Vite's server options](https://vite.dev/config/server-options).
No physical headset acceptance or performance claim has been made. Hand tracking,
in-headset typing, locomotion, and AR passthrough are not implemented.

## Build, validation and automation

From `frontend`:

```powershell
npm run build      # strict TypeScript check + production dist/
npm test           # shared contract, atomic reducer, projection mathematics
npx playwright install chromium
npm run test:e2e   # starts isolated test backend :8001 and Vite :5173
```

Stop any existing Vite on port 5173 first. Browser tests never reuse your running
backend or demo data: `tests/serve_backend.py` uses an in-memory graph, loopback,
and an intentionally public test-only token. The backend virtual environment must
exist at `backend/.venv`. On Linux create it there with `python -m venv backend/.venv`.
If `PLAYWRIGHT_BROWSERS_PATH` is set during browser installation, use the same setting
when running tests. Screenshots and traces/results go under ignored `test-results/`.

`npm run preview` can smoke-test `dist/` through the same proxy locally; it is not a
production server. Deploy static `dist/` behind a proper HTTPS reverse proxy forwarding
`/api` and WS upgrades at `/ws` to a single backend worker, preserving Origin. Configure
the backend's exact public origin and real account authentication before public hosting.

The frame HUD reports a rolling 120-frame p95 of animation-callback intervals and
Three.js geometry/texture counts. It measures neither GPU time nor response-to-photon
latency. Replaced scene resources are disposed, slider rebuilds retain no geometry list,
and the pixel ratio is capped at 2. The scene stays within the backend's 30-object limit.
See [frontend verification](../docs/FRONTEND.md) for results and remaining device checks.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| HTTP 401 | Paste the current backend terminal's token; restarting demo rotates it. |
| HTTP 404 after reconnect | Backend restart discarded the lesson. Select Connect for a new lesson. |
| WS closes / 1008 | Exact page Origin must be in `LUCID_ALLOWED_ORIGINS`; token must match. Use localhost by default. |
| HTTP 500 / user missing | Demo DB must contain the provisioned user; an unrelated existing `demo.db` is not reseeded. Back it up and choose the correct setup. |
| Port 5173 busy | Stop the old dev server. Strict port selection prevents silently changing the allowed Origin. |
| Proxy ECONNREFUSED | Start backend :8000 or set `LUCID_BACKEND_URL` in `.env.local` and restart Vite. |
| VR unavailable | Verify trusted HTTPS, secure context, headset/browser WebXR support, and immersive VR permissions. Desktop rendering still works. |
| Blank canvas | Enable WebGL/hardware acceleration; inspect the graphics error and browser console. |
| Stale scene error | Client restores a snapshot automatically; submit your next question once Connected. |
| Capacity reached | Disconnect unused lessons. Abrupt page shutdown cannot guarantee HTTP deletion; restart local backend to clear process-local lessons. |
| Bundler access denied in Codex sandbox | The Windows bundler may need ancestor-directory access; run the command from a normal development terminal or approve the scoped tooling execution. |

## Files to extend

The learning-day panel offers **Learn something new** and an optional **Review due topics**
warm-up, backed by FSRS. Suggested topics show mastery separately from estimated recall and the
next review date. **Active recall** asks before showing the explanation; **Skip** never awards
mastery. Guided lessons pause at predictions and small steps; **Continue** cannot dismiss a
pending question. AI synthesis and teach-back tasks require the backend lesson author.

For a spatial example, explore `3D graph of a quadratic's complex roots`. The scene contains a
sampled surface, three labeled axes and roots on the complex input plane. Drag to orbit, or
choose **Move model** / **Rotate model** for transform handles. With **Whole model** unchecked,
select a scene object to manipulate it separately. **Reset arrangement** restores placement.
In VR, aim at the model and squeeze the grip to move and rotate it; release to place it.
Supported hand tracking uses pinch. Trigger presses still operate slider buttons. Object
placement is local and retained across steps/highlights; it resets for a different concept.

`src/geometry.ts` handles bounded surface triangulation; `src/manipulation.ts` owns exclusive
grabs and world-transform-preserving pickup/release. Automated tests verify their math, while
physical headset/controller/hand-tracking behavior still needs a hardware acceptance run.

| File | Responsibility |
| --- | --- |
| `src/protocol.ts` | Shared schema validation, immutable reducer, local projection recipe |
| `src/client.ts` | Auth, lesson lifecycle, deadlines, WS synchronization/recovery |
| `src/renderer.ts` | Primitive rendering, disposal, camera, captions, XR controller interactions |
| `src/main.ts`, `src/style.css` | Accessible desktop forms, explanation beats, local controls and layout |
| `vite.config.ts` | Same-origin proxy, strict port, optional trusted TLS |
| `tests/` | Protocol invariants and real-backend browser integration |
