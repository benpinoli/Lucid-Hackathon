# LUCID scene and tutor backend

Run commands from the repository root in PowerShell. Requires Python 3.12+.
The memory package remains owned by the memory agent; this service imports its public API
using `PYTHONPATH=src;.`. It does not maintain a second graph implementation or migration tree.

```powershell
python -m venv backend/.venv
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
The result includes a three-beat authored explanation and validated `scene.replace` event.
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
Voice uses text fallback; wave-packet and barrier recipes remain unimplemented. See [progress](../docs/PROGRESS.md).
