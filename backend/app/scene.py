"""Safe retained scene contract. No executable expressions or remote assets."""
from __future__ import annotations

from collections import OrderedDict
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .expressions import ExpressionError, parse as parse_expression

MAX_BYTES = 16_384
MAX_OBJECTS = 30
Id = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
Number = Annotated[float, Field(ge=-1000, le=1000)]
Point = Annotated[list[Number], Field(min_length=2, max_length=3)]
Text = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^<>\x00-\x1f]*$")]


def _expression(value: str) -> str:
    try:
        parse_expression(value)
    except ExpressionError as exc:
        raise ValueError(f"Invalid expression: {exc}") from exc
    return value


# Safe arithmetic only (see expressions.py); renderers evaluate it with slider parameters.
Expr = Annotated[str, Field(min_length=1, max_length=160), AfterValidator(_expression)]
ExprPoint2 = Annotated[list[Expr], Field(min_length=2, max_length=3)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Axes(StrictModel):
    primitive: Literal["axes2d"] = "axes2d"
    extent: Annotated[float, Field(gt=0, le=100)] = 5.0


class Axes3D(StrictModel):
    primitive: Literal["axes3d"] = "axes3d"
    extent: Annotated[float, Field(gt=0, le=6)] = 4.0
    labels: Annotated[list[Text], Field(min_length=3, max_length=3)] = Field(default_factory=lambda: ["x", "y", "z"])


class Arrow(StrictModel):
    primitive: Literal["vector_arrow"] = "vector_arrow"
    start: Point = Field(default_factory=lambda: [0.0, 0.0])
    end: Point

    @model_validator(mode="after")
    def dimensions(self):
        if len(self.start) != len(self.end):
            raise ValueError("Arrow endpoints must have matching dimensions")
        return self


class Curve(StrictModel):
    primitive: Literal["line_curve"] = "line_curve"
    points: Annotated[list[Point], Field(min_length=2, max_length=128)]

    @model_validator(mode="after")
    def dimensions(self):
        if len({len(p) for p in self.points}) > 1:
            raise ValueError("Curve points must have matching dimensions")
        return self


class Label(StrictModel):
    primitive: Literal["text_label"] = "text_label"
    text: Text
    position: Point = Field(default_factory=lambda: [0.0, 0.0])


class ExprCurve(StrictModel):
    """Parametric curve (x(t), y(t)); other variables are param sliders."""
    primitive: Literal["expr_curve"] = "expr_curve"
    x: Expr
    y: Expr
    z: Expr | None = None
    t_min: Number
    t_max: Number
    samples: Annotated[int, Field(ge=2, le=200)] = 96

    @model_validator(mode="after")
    def domain(self):
        if not self.t_min < self.t_max:
            raise ValueError("t_min must be below t_max")
        return self


class ExprArrow(StrictModel):
    primitive: Literal["expr_arrow"] = "expr_arrow"
    start: ExprPoint2 = Field(default_factory=lambda: ["0", "0"])
    end: ExprPoint2

    @model_validator(mode="after")
    def dimensions(self):
        if len(self.start) != len(self.end):
            raise ValueError("Arrow endpoints must have matching dimensions")
        return self


class ExprSurface(StrictModel):
    primitive: Literal["expr_surface"] = "expr_surface"
    x: Expr
    y: Expr
    z: Expr
    u_min: Number
    u_max: Number
    v_min: Number
    v_max: Number
    samples: Annotated[int, Field(ge=8, le=40)] = 24

    @model_validator(mode="after")
    def domain(self):
        if not self.u_min < self.u_max or not self.v_min < self.v_max:
            raise ValueError("Surface parameter intervals must increase")
        return self


class ExprPoint(StrictModel):
    primitive: Literal["expr_point"] = "expr_point"
    at: ExprPoint2
    text: Text | None = None


class Slider(StrictModel):
    primitive: Literal["slider"] = "slider"
    # Authored projection bindings, or a diagram parameter (single letter; e and t are reserved).
    binding: Literal["projection.angle", "projection.length"] | Annotated[str, Field(pattern=r"^param\.[a-df-su-z]$")]
    label: Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[^<>\x00-\x1f]*$")] | None = None
    minimum: Number
    maximum: Number
    value: Number

    @model_validator(mode="after")
    def bounds(self):
        if not self.minimum < self.maximum or not self.minimum <= self.value <= self.maximum:
            raise ValueError("Invalid slider domain")
        if self.binding == "projection.length" and self.minimum < 0:
            raise ValueError("Length must be nonnegative")
        return self


Primitive = Annotated[Axes | Axes3D | Arrow | Curve | Label | Slider | ExprCurve | ExprArrow | ExprPoint | ExprSurface,
                      Field(discriminator="primitive")]


class Node(StrictModel):
    id: Id
    content: Primitive
    style: Literal["primary", "secondary", "muted"] = "primary"
    visible: bool = True
    coordinate_system: Literal["lesson_xy", "lesson_xyz"] = "lesson_xy"


class Replace(StrictModel):
    op: Literal["scene.replace"] = "scene.replace"
    nodes: Annotated[list[Node], Field(max_length=MAX_OBJECTS)]


class Create(StrictModel):
    op: Literal["node.create"] = "node.create"
    node: Node


class Patch(StrictModel):
    """Atomic typed node replacement; ID and primitive type remain immutable."""
    op: Literal["node.patch"] = "node.patch"
    node: Node


class Remove(StrictModel):
    op: Literal["node.remove"] = "node.remove"
    object_id: Id


Operation = Annotated[Replace | Create | Patch | Remove, Field(discriminator="op")]


class Event(StrictModel):
    schema_version: Literal[1] = 1
    event_id: Id
    lesson_id: Id
    scene_id: Id
    turn_id: Id
    utterance_id: Id
    turn_epoch: Annotated[int, Field(ge=1)]
    seq: Annotated[int, Field(ge=1)]
    base_revision: Annotated[int, Field(ge=0)]
    operation: Operation
    cue: Literal["immediate", "manual"] = "immediate"


def decode_event(raw: str) -> Event:
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Scene event exceeds 16 KiB")
    return Event.model_validate_json(raw)


class SceneState:
    def __init__(self, lesson_id: str):
        self.lesson_id = lesson_id
        self.scene_id = "scene_" + lesson_id
        self.epoch = 0
        self.seq = 0
        self.revision = 0
        self.nodes: dict[str, Node] = {}
        self.seen: OrderedDict[str, str] = OrderedDict()

    def apply(self, event: Event) -> bool:
        raw = event.model_dump_json()
        if len(raw.encode()) > MAX_BYTES:
            raise ValueError("Scene event exceeds 16 KiB")
        if event.lesson_id != self.lesson_id or event.scene_id != self.scene_id:
            raise ValueError("Wrong scene scope")
        if event.event_id in self.seen:
            if self.seen[event.event_id] != raw:
                raise ValueError("Event ID reused with different content")
            return False
        if event.turn_epoch != self.epoch:
            raise ValueError("Stale or uncommitted epoch")
        if event.seq != self.seq + 1 or event.base_revision != self.revision:
            raise ValueError("Snapshot required: sequence or revision mismatch")
        updated = dict(self.nodes)
        op = event.operation
        if isinstance(op, Replace):
            updated = {node.id: node for node in op.nodes}
            if len(updated) != len(op.nodes):
                raise ValueError("Duplicate object IDs")
        elif isinstance(op, Create):
            if op.node.id in updated:
                raise ValueError("Object already exists")
            updated[op.node.id] = op.node
        elif isinstance(op, Patch):
            old = updated.get(op.node.id)
            if old is None or old.content.primitive != op.node.content.primitive:
                raise ValueError("Patch requires existing object of same primitive")
            updated[op.node.id] = op.node
        else:
            updated.pop(op.object_id, None)
        if len(updated) > MAX_OBJECTS:
            raise ValueError("Scene object budget exceeded")
        self.nodes = updated
        self.seq = event.seq
        self.revision += 1
        self.seen[event.event_id] = raw
        if len(self.seen) > 256:
            self.seen.popitem(last=False)
        return True

    def snapshot(self):
        return {"schema_version": 1, "lesson_id": self.lesson_id, "scene_id": self.scene_id,
                "turn_epoch": self.epoch, "seq": self.seq, "scene_revision": self.revision,
                "nodes": [node.model_dump() for node in self.nodes.values()]}


def export_schema() -> dict:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            **TypeAdapter(Event).json_schema()}
