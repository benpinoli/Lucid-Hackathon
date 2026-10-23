# Desktop / WebXR implementation and verification

Date: 2026-10-22 (America/Denver).

The repository now includes a connected browser-based desktop and immersive-VR
starter in `frontend/`. Follow [the runbook](../frontend/README.md) for installation,
local startup, TLS/headset access, integration details, troubleshooting and extension points.
No changes were made to graph algorithms, score semantics, graph migrations, or backend
authentication. `backend/tools/dev.py` adds a local demo launcher; it is not a production
authentication system. Nothing was deployed, committed or published by this implementation.

## Implemented behavior

- Desktop studio with private-token input, explicit lesson creation/deletion, question
  form, authored explanation beats, focus highlighting, status/error display and reset camera.
- Three.js rendering for the five supported primitives: axes, vector arrows, line curves,
  canvas text labels and local sliders. `lesson_xy` coordinates map onto a world-space
  board at 0.23 meters/unit, centered 2.5 meters forward and 1.45 meters above the floor.
- WebXR immersive-VR entry and animation loop, world-space captions, two controller
  rays and trigger-operated angle buttons. Questions and beat selection use the desktop UI.
- Shared JSON Schema validation, schema-default materialization, additional slider and
  object checks, atomic scene updates, scope/epoch/sequence/revision guards and snapshot recovery.
- First-message WebSocket authentication, receipt ACKs without an ACK feedback loop,
  heartbeat snapshots, bounded reconnects, handshake/response deadlines, and no automatic
  retry of potentially committed user commands. Tokens stay out of storage and URLs.
- Local projection math changes only this view. New turns restore authored geometry.
  Every replaced GPU geometry, material and texture is disposed; the page teardown also
  releases controls, observers, animation loop and renderer. In-memory backend lessons are
  deleted on explicit disconnect; abrupt browser termination can still leave a lesson slot.
- Same-origin HTTP/WS proxy, optional trusted TLS, dependency lockfile, isolated real-backend
  browser tests and a separate frontend CI workflow.

## Verification completed

Local environment: Windows, Node 24.15.0, Python 3.14 environment already provided by
the repository, Chromium 156.0.8078.4 through Playwright. Remote CI has not run.

| Check | Result |
| --- | --- |
| `npm run build` | Passed strict TypeScript and production bundling |
| `npm test` | 17 tests passed |
| `npm run test:e2e` | 3 Chromium tests passed, approximately 2.1 minutes total |
| 500 rendered scene replacements | Passed; retained 6 scene objects, reached revision 501; reported geometry and texture counts equal before/after |
| Browser integration | Passed invalid/valid auth, lesson turn, rendered canvas, local angle control, socket reconnect to same retained scene, interrupt, unsupported-topic fallback and explicit disconnect |
| Narrow layout | Passed no-horizontal-overflow check at 390 × 844 |
| Screenshot review | Reviewed desktop studio at 1440 × 1000; enlarged scene label text after first review |
| Backend regression | 17 tests passed; existing Starlette/httpx deprecation warning remains |
| Memory graph regression | 49 tests passed |
| Shared schema drift | Both schemas match their Python contracts |
| Python lint | `backend/tools/dev.py` passes Ruff |
| Dependency audit | Zero vulnerabilities after updating Vitest to 5.0.5 |

Unit tests cover wrong scene scope, stale epochs, duplicate replay, sequence/revision
gaps, invalid lifecycle operations, unknown/unsafe primitives, nonfinite numeric values,
duplicate IDs, slider bounds, schema defaults, 500 bounded reducer replacements and
projection math at right/negative angles and changed vector magnitudes.

Browser stress traffic is paced below the backend's 20-command/second limit including
ACKs. Its timeout permits slower software-rendered CI hosts. Resource-count equality
shows bounded Three.js allocations for this scene; it does not establish GPU-driver
memory behavior or headset thermal stability. The screenshot is generated under
ignored `frontend/test-results/desktop.png` when the browser suite runs.

The bundle is approximately 670 kB minified / 178 kB gzip and Vite reports its default
500 kB chunk-size warning. This starter loads Three.js and schema validation together;
future work can split the renderer if startup measurements justify it.

## Remaining acceptance work

1. Use a physical headset on the intended network with a trusted TLS certificate and
   exact allowed Origin. Record headset model, browser/firmware version and network setup.
2. Confirm Enter/Exit VR, eye height, scene/caption legibility, both controller rays,
   positive/negative angle buttons, and desktop camera restoration after leaving VR.
3. Measure representative frame intervals, visible turn latency, GPU/driver resource use,
   and sustained thermal behavior. The HUD is callback-interval p95, not GPU time or
   response-to-photon timing. No headset performance measurement has been recorded.
4. Test actual network loss and backend restart on-device. Browser automation verified
   socket-close recovery; it does not substitute for Wi-Fi interruption or TLS validation.
5. Decide whether to add in-headset text entry, in-headset beat selection, hand tracking,
   voice/audio scheduling, graph browsing and assessment/history UI. Those are not part
   of the current connected lesson starter. No native desktop packaging or AR mode exists.

Implementation follows the [Three.js WebXR guide](https://threejs.org/manual/en/webxr-basics.html)
and [Vite proxy/TLS configuration](https://vite.dev/config/server-options).
