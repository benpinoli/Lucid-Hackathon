"""Run from repository root: python -m uvicorn backend.app.main:app --port 8000."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from time import monotonic
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError as SchemaError

from memory_graph import Conflict, GraphError, MemoryGraph, NotFound, ResourceLimit

from .scene import Id, MAX_BYTES, StrictModel
from .author import ClaudeAuthor
from .goals import Curriculum, Goals, GoalError
from .tutor import Tutor
from .learning import plan
from .planner import ClaudePlanner
from .service import TutorService
from .voice import OpenAIRealtime, VoiceError

FROM_ENV = object()


class Turn(StrictModel):
    type: Literal["turn"] = "turn"
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    expected_epoch: Annotated[int, Field(ge=0)]
    sources: Annotated[list[Id], Field(max_length=64)] = Field(default_factory=list)
    target: Id | None = None
    concept: Id | None = None  # Teach this curriculum concept (a route step).
    route_to: Id | None = None  # Show the learner's route map to this goal.


class GoalRequest(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=500)]


class Answer(StrictModel):
    concept_id: Id
    choice: Annotated[int, Field(ge=0, le=9)] | None  # None means "not sure".


class Diagnostic(StrictModel):
    answers: Annotated[list[Answer], Field(min_length=1, max_length=16)]
    expected_revision: Annotated[int, Field(ge=0)]
    idempotency_key: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")]


class Progress(StrictModel):
    concept_id: Id
    score: Annotated[float, Field(ge=1, le=10)]
    previous_concept_id: Id | None = None
    expected_revision: Annotated[int, Field(ge=0)]
    idempotency_key: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")]


class TutorAction(StrictModel):
    type: Literal["tutor"] = "tutor"
    action: Literal["goal", "answer", "confused", "question", "continue", "explore", "review", "deeper", "recall", "warmup", "skip"]
    text: Annotated[str, Field(max_length=2000)] = ""
    expected_epoch: Annotated[int, Field(ge=0)]


class Control(StrictModel):
    type: Literal["scene.snapshot_request", "interrupt", "scene.ack"]
    expected_epoch: Annotated[int, Field(ge=0)] = 0
    seq: Annotated[int, Field(ge=0)] = 0


class PathRequest(StrictModel):
    sources: Annotated[list[Id], Field(min_length=1, max_length=64)]
    target: Id
    formula_id: str | None = None


class Assessment(StrictModel):
    concept_id: Id
    score: Annotated[float, Field(ge=1, le=10)]
    idempotency_key: Id
    expected_revision: Annotated[int, Field(ge=0)]


def create_app(graph=None, tokens=None, origins=None, voice=FROM_ENV, planner=FROM_ENV, author=FROM_ENV):
    @asynccontextmanager
    async def lifespan(app):
        credentials = tokens if tokens is not None else json.loads(os.getenv("LUCID_TOKENS_JSON", "{}"))
        if not credentials or not all(isinstance(k, str) and len(k) >= 24 and isinstance(v, str)
                                      for k, v in credentials.items()):
            raise RuntimeError("Configure LUCID_TOKENS_JSON: {long random token: provisioned human UUID}")
        app.state.tokens = credentials
        app.state.graph = graph or MemoryGraph.open(os.getenv("LUCID_DATABASE_URL", "sqlite:///lucid.db"))
        # None keeps the keyword-routed authored lessons; no provider is ever required.
        app.state.planner = ClaudePlanner.from_env() if planner is FROM_ENV else planner
        planner_fn = app.state.planner.plan if app.state.planner else plan
        curriculum = Curriculum().sync(app.state.graph)
        resolver = app.state.planner.resolver(curriculum) if app.state.planner else None
        app.state.curriculum = curriculum
        goals = Goals(app.state.graph, curriculum, planner_fn, resolver)
        if author is not FROM_ENV:
            app.state.author = author
        else:
            app.state.author = (ClaudeAuthor(app.state.planner.client, app.state.planner.model)
                                if app.state.planner else None)
        app.state.service = TutorService(app.state.graph, planner_fn, goals,
                                         Tutor(app.state.graph, goals, planner_fn, app.state.author))
        # None disables voice; text lessons keep working without any provider.
        app.state.voice = OpenAIRealtime.from_env() if voice is FROM_ENV else voice
        try:
            yield
        finally:
            if graph is None:
                app.state.graph.close()

    app = FastAPI(title="LUCID VR backend", version="0.1.0", lifespan=lifespan)
    allowed_origins = origins if origins is not None else os.getenv(
        "LUCID_ALLOWED_ORIGINS", "https://localhost:5173,http://localhost:5173").split(",")

    def resolve(token):
        for candidate, user in app.state.tokens.items():
            if secrets.compare_digest(token, candidate):
                return user
        raise HTTPException(401, "Invalid credential")

    def identity(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer credential required")
        return resolve(authorization[7:])

    def scoped(user, user_id):
        if user != user_id:
            raise HTTPException(403, "User scope mismatch")

    @app.exception_handler(GraphError)
    async def domain_error(request, exc):
        status = 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, Conflict) else 422
        if isinstance(exc, ResourceLimit):
            status = 413
        return JSONResponse({"error": type(exc).__name__, "detail": str(exc)}, status_code=status)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            # Stream with a bound; do not trust Content-Length or buffer unbounded input.
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    return JSONResponse({"error": "Request exceeds 16 KiB"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "scene_schema_version": 1,
                "voice": "realtime" if app.state.voice else "text_only",
                "planner": "claude" if app.state.planner else "authored",
                "author": "claude" if app.state.author else "authored",
                "formula_id": app.state.graph.methodology.formula_id,
                "epsilon": app.state.graph.methodology.epsilon}

    @app.post("/api/voice/sessions", status_code=201)
    def voice_session(user=Depends(identity)):
        if not app.state.voice:
            raise HTTPException(503, "Voice is not configured; set OPENAI_API_KEY on the backend")
        try:
            return app.state.voice.create_credential()
        except VoiceError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/lessons", status_code=201)
    def create_lesson(user=Depends(identity)):
        return app.state.service.create(user)

    @app.get("/api/lessons/{lesson_id}/snapshot")
    def snapshot(lesson_id: str, user=Depends(identity)):
        with app.state.service.lock:
            return app.state.service.owned(user, lesson_id).snapshot()

    @app.delete("/api/lessons/{lesson_id}", status_code=204)
    def delete_lesson(lesson_id: str, user=Depends(identity)):
        with app.state.service.lock:
            app.state.service.owned(user, lesson_id)
            del app.state.service.lessons[lesson_id]
            app.state.service.sessions.pop(lesson_id, None)

    @app.post("/api/lessons/{lesson_id}/turns")
    def turn(lesson_id: str, body: Turn, user=Depends(identity)):
        return app.state.service.turn(user, lesson_id, body.text, body.expected_epoch,
                                      body.sources, body.target, body.concept, body.route_to)

    @app.post("/api/lessons/{lesson_id}/tutor")
    def tutor(lesson_id: str, body: TutorAction, user=Depends(identity)):
        return app.state.service.tutor_turn(user, lesson_id, body.action, body.text, body.expected_epoch)

    @app.get("/api/me")
    def me(user=Depends(identity)):
        return {"user_id": user, "revision": app.state.graph.human(user).revision}

    @app.get("/api/me/reviews")
    def reviews(user=Depends(identity)):
        return app.state.service.tutor.reviews.suggestions(user)

    @app.get("/api/curriculum")
    def curriculum(user=Depends(identity)):
        return app.state.curriculum.listing()

    @app.get("/api/users/{user_id}/lessons")
    def lesson_library(user_id: str, user=Depends(identity)):
        scoped(user, user_id)
        return app.state.service.tutor.library.listing(user)

    @app.post("/api/users/{user_id}/goals")
    def goal(user_id: str, body: GoalRequest, user=Depends(identity)):
        scoped(user, user_id)
        return app.state.service.goals.start(user, body.text)

    @app.post("/api/users/{user_id}/goals/{target_id}/diagnostic")
    def diagnostic(user_id: str, target_id: str, body: Diagnostic, user=Depends(identity)):
        scoped(user, user_id)
        answers = [(a.concept_id, a.choice) for a in body.answers]
        return app.state.service.write_goal("diagnose", user, target_id, answers,
                                            body.expected_revision, body.idempotency_key)

    @app.get("/api/users/{user_id}/goals/{target_id}/route")
    def route(user_id: str, target_id: str, user=Depends(identity)):
        scoped(user, user_id)
        return app.state.service.goals.route(user, target_id)

    @app.post("/api/users/{user_id}/progress")
    def progress(user_id: str, body: Progress, user=Depends(identity)):
        scoped(user, user_id)
        return app.state.service.write_goal("progress", user, body.concept_id, body.score,
                                            body.previous_concept_id, body.expected_revision,
                                            body.idempotency_key)

    @app.post("/api/users/{user_id}/learning-path")
    def path(user_id: str, body: PathRequest, user=Depends(identity)):
        scoped(user, user_id)
        return app.state.service.path(user, body.sources, body.target, body.formula_id)

    @app.post("/api/users/{user_id}/assessments")
    def assessment(user_id: str, body: Assessment, user=Depends(identity)):
        scoped(user, user_id)
        return app.state.service.assess(user, body)

    @app.get("/api/users/{user_id}/concepts/{concept_id}/history")
    def history(user_id: str, concept_id: str, user=Depends(identity)):
        scoped(user, user_id)
        return app.state.graph.history(kind="understanding", human_id=user, concept=concept_id)

    @app.get("/api/recipes/{recipe_id}/manifest")
    def manifest(recipe_id: str, user=Depends(identity)):
        if recipe_id not in {"vector_projection_v1", "dijkstra_v1", "mean_value_v1", "whiteboard_v1"}:
            raise HTTPException(404, "Unknown recipe")
        bindings = ["projection.angle", "projection.length"] if recipe_id == "vector_projection_v1" else []
        return {"recipe_id": recipe_id, "schema_version": 1, "local_bindings": bindings,
                "execution": "authored_client_runtime"}

    @app.websocket("/ws/scenes")
    async def scenes(ws: WebSocket, lesson_id: str):
        if ws.headers.get("origin") not in allowed_origins:
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            # Browser WS cannot set Authorization. First message authenticates; no URL secrets.
            raw = await asyncio.wait_for(ws.receive_text(), timeout=5)
            if len(raw.encode()) > 1024:
                raise ValueError("Authentication message too large")
            auth = json.loads(raw)
            if set(auth) != {"token"} or not isinstance(auth["token"], str):
                raise ValueError("Expected token")
            user = resolve(auth["token"])
            service = app.state.service
            with service.lock:
                initial = service.owned(user, lesson_id).snapshot()
            await ws.send_json({"type": "scene.snapshot", "snapshot": initial})
            window, count = monotonic(), 0
            while True:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60)
                now = monotonic()
                if now - window >= 1:
                    window, count = now, 0
                count += 1
                if count > 20:
                    await ws.close(code=1008)
                    return
                if len(raw.encode()) > MAX_BYTES:
                    await ws.close(code=1009)
                    return
                try:
                    data = json.loads(raw)
                    if data.get("type") == "tutor":
                        req = TutorAction.model_validate(data)
                        result = await asyncio.to_thread(service.tutor_turn, user, lesson_id,
                                                         req.action, req.text, req.expected_epoch)
                        await ws.send_json({"type": "lesson.turn", **result})
                    elif data.get("type") == "turn":
                        req = Turn.model_validate(data)
                        result = await asyncio.to_thread(service.turn, user, lesson_id,
                            req.text, req.expected_epoch, req.sources, req.target,
                            req.concept, req.route_to)
                        await ws.send_json({"type": "lesson.turn", **result})
                    else:
                        control = Control.model_validate(data)
                        with service.lock:
                            state = service.owned(user, lesson_id)
                            if control.type == "interrupt":
                                current = service.interrupt(user, lesson_id, control.expected_epoch)
                            else:
                                current = state.snapshot()
                            if control.type == "scene.ack" and control.seq > state.seq:
                                raise ValueError("Cannot acknowledge an unsent sequence")
                        await ws.send_json({"type": "scene.snapshot", "snapshot": current})
                except GoalError as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                except (SchemaError, ValueError, GraphError, AttributeError):
                    await ws.send_json({"type": "error", "detail": "Invalid or stale command; request snapshot"})
        except (WebSocketDisconnect, TimeoutError):
            pass
        except (HTTPException, GraphError, ValueError, TypeError):
            await ws.close(code=1008)

    return app


app = create_app()
