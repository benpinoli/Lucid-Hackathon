"""Diagram language: what an author (Claude or a person) writes; LUCID computes the geometry.

A diagram is formulas, arrows, points, labels and an optional node-edge graph, with up to
three sliders that bind single-letter parameters. Compilation validates every expression,
checks the default state is in view and within the scene budget, and maps each beat's focus
to scene node IDs. Formulas stay symbolic in the scene, so sliders re-plot on the client
without a network call.
"""
from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .expressions import ExpressionError, compile_expression
from .learning import LessonError, LessonPlan, TeachingBeat, _layout
from .scene import (Axes, Curve, ExprArrow, ExprCurve, ExprPoint, Label, MAX_OBJECTS, Node, Slider)

ID = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
VIEW = 6.0  # Anything at the default slider values must sit within this box.
RESERVED = {"e", "t"}  # e is Euler's number; t is the curve parameter.


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DSlider(_Spec):
    name: str
    label: str
    min: float
    max: float
    value: float


class DCurve(_Spec):
    id: str
    x: str
    y: str
    t_min: float
    t_max: float
    style: Literal["primary", "secondary", "muted"]


class DArrow(_Spec):
    id: str
    start_x: str
    start_y: str
    end_x: str
    end_y: str
    style: Literal["primary", "secondary", "muted"]


class DPoint(_Spec):
    id: str
    x: str
    y: str
    text: str | None


class DLabel(_Spec):
    id: str
    text: str
    x: float
    y: float


class DGraphNode(_Spec):
    id: str
    label: str


class DGraphEdge(_Spec):
    a: str
    b: str
    weight: float | None


class DGraph(_Spec):
    nodes: list[DGraphNode]
    edges: list[DGraphEdge]
    highlight: list[str]


class Diagram(_Spec):
    axes: bool
    sliders: list[DSlider]
    curves: list[DCurve]
    arrows: list[DArrow]
    points: list[DPoint]
    labels: list[DLabel]
    graph: DGraph | None


class DBeat(_Spec):
    say: str
    focus: list[str]


class DiagramLesson(_Spec):
    title: str
    beats: list[DBeat]
    diagram: Diagram


class DiagramError(LessonError):
    """A diagram LUCID cannot draw faithfully; the message is safe to give an author model."""


def _check(condition, message):
    if not condition:
        raise DiagramError(message)


def _expr(source, allowed, where):
    try:
        function, names = compile_expression(source)
    except ExpressionError as exc:
        raise DiagramError(f"{where}: {exc}") from exc
    unknown = names - allowed
    _check(not unknown, f"{where} uses undefined parameter(s) {sorted(unknown)}; add a slider or use a number")
    return function


def _inside(*values):
    return all(math.isfinite(v) and abs(v) <= VIEW for v in values)


def compile_lesson(lesson: DiagramLesson, recipe_id="diagram_v1", generated=False) -> LessonPlan:
    d = lesson.diagram
    nodes, focus = [], {}
    seen = set()

    def claim(element_id):
        _check(bool(ID.match(element_id)), f"Element id {element_id!r} must be lowercase letters, digits, _")
        _check(element_id not in seen, f"Duplicate element id {element_id!r}")
        seen.add(element_id)

    _check(len(d.sliders) <= 3, "Use at most 3 sliders")
    params = {}
    for slider in d.sliders:
        _check(len(slider.name) == 1 and slider.name.isalpha() and slider.name.islower()
               and slider.name not in RESERVED, "Slider names are one lowercase letter other than e or t")
        _check(slider.name not in params, f"Duplicate slider {slider.name}")
        _check(slider.min < slider.max and slider.min <= slider.value <= slider.max
               and all(abs(v) <= 1000 for v in (slider.min, slider.max)), f"Slider {slider.name} needs min <= value <= max")
        params[slider.name] = slider.value
    allowed = set(params)

    title = lesson.title.strip()[:60] or "Diagram"
    nodes.append(Node(id="title", content=Label(text=title, position=[0.0, 4.6])))
    focus["title"] = ["title"]
    if d.axes:
        nodes.append(Node(id="axes", content=Axes()))
        focus["axes"] = ["axes"]
    for curve in d.curves:
        claim(curve.id)
        fx = _expr(curve.x, allowed | {"t"}, f"Curve {curve.id} x")
        fy = _expr(curve.y, allowed | {"t"}, f"Curve {curve.id} y")
        _check(curve.t_min < curve.t_max, f"Curve {curve.id} needs t_min < t_max")
        samples = [{**params, "t": curve.t_min + (curve.t_max - curve.t_min) * i / 63} for i in range(64)]
        inside = sum(_inside(fx(s), fy(s)) for s in samples)
        _check(inside >= 32, f"Curve {curve.id} is mostly outside the view (-{VIEW}..{VIEW}) at the default values")
        nodes.append(Node(id=curve.id, style=curve.style, content=ExprCurve(
            x=curve.x, y=curve.y, t_min=curve.t_min, t_max=curve.t_max)))
        focus[curve.id] = [curve.id]
    for arrow in d.arrows:
        claim(arrow.id)
        coords = [_expr(e, allowed, f"Arrow {arrow.id}")(params)
                  for e in (arrow.start_x, arrow.start_y, arrow.end_x, arrow.end_y)]
        _check(_inside(*coords), f"Arrow {arrow.id} must lie within the view at the default values")
        nodes.append(Node(id=arrow.id, style=arrow.style, content=ExprArrow(
            start=[arrow.start_x, arrow.start_y], end=[arrow.end_x, arrow.end_y])))
        focus[arrow.id] = [arrow.id]
    for point in d.points:
        claim(point.id)
        coords = [_expr(e, allowed, f"Point {point.id}")(params) for e in (point.x, point.y)]
        _check(_inside(*coords), f"Point {point.id} must lie within the view at the default values")
        nodes.append(Node(id=point.id, content=ExprPoint(at=[point.x, point.y], text=point.text or None)))
        focus[point.id] = [point.id]
    for label in d.labels:
        claim(label.id)
        _check(abs(label.x) <= 5.5 and abs(label.y) <= 5.5, f"Label {label.id} must be within -5.5..5.5")
        nodes.append(Node(id=label.id, content=Label(text=label.text, position=[label.x, label.y])))
        focus[label.id] = [label.id]
    if d.graph is not None:
        nodes += _graph(d.graph, claim, focus)
    for slider in d.sliders:
        nodes.append(Node(id=f"slider_{slider.name}", content=Slider(
            binding=f"param.{slider.name}", label=slider.label[:40], minimum=slider.min,
            maximum=slider.max, value=slider.value)))
    _check(len(nodes) <= MAX_OBJECTS, f"Too many objects ({len(nodes)}); the limit is {MAX_OBJECTS}")
    _check(1 <= len(lesson.beats) <= 6, "Use 1 to 6 beats")
    beats = []
    for i, beat in enumerate(lesson.beats, start=1):
        say = beat.say.strip()
        _check(0 < len(say) <= 400 and not re.search(r"[<>\x00-\x1f]", say), "Each beat says 1-400 plain characters")
        # Unknown focus IDs are dropped: highlighting is a hint, not a correctness claim.
        ids = [node_id for key in beat.focus if key in focus for node_id in focus[key]]
        beats.append(TeachingBeat(beat_id=f"b{i}", say_text=say, focus_ids=ids))
    return LessonPlan(recipe_id=recipe_id, nodes=nodes, beats=beats,
                      planned_by="claude" if generated else "authored", generated=generated)


def _graph(graph: DGraph, claim, focus):
    _check(2 <= len(graph.nodes) <= 7, "A graph has 2 to 7 nodes")
    _check(len(graph.edges) <= 10, "A graph has at most 10 edges")
    ids = [n.id for n in graph.nodes]
    for node in graph.nodes:
        claim(node.id)
        _check(0 < len(node.label) <= 20, f"Graph node {node.id} needs a label of 1-20 characters")
    pairs = set()
    for edge in graph.edges:
        _check(edge.a in ids and edge.b in ids and edge.a != edge.b, "Graph edges join two listed nodes")
        _check(frozenset((edge.a, edge.b)) not in pairs, "One edge per node pair")
        _check(edge.weight is None or math.isfinite(edge.weight), "Edge weights are numbers or null")
        pairs.add(frozenset((edge.a, edge.b)))
    _check(set(graph.highlight) <= set(ids), "Highlighted nodes must be graph nodes")
    positions = _layout(ids, ids[0])
    on_path = {frozenset(pair) for pair in zip(graph.highlight, graph.highlight[1:])}
    nodes, everything = [], []
    for node in graph.nodes:
        x, y = positions[node.id]
        circle = [[round(x + 0.45 * math.cos(math.pi * i / 8), 4), round(y + 0.45 * math.sin(math.pi * i / 8), 4)]
                  for i in range(17)]
        nodes.append(Node(id=f"g_{node.id}", style="secondary" if node.id in graph.highlight else "primary",
                          content=Curve(points=circle)))
        nodes.append(Node(id=f"gl_{node.id}", content=Label(text=node.label, position=[x, round(y + (0.9 if y >= 0 else -0.9), 4)])))
        focus[node.id] = [f"g_{node.id}", f"gl_{node.id}"]
        everything += focus[node.id]
    for edge in graph.edges:
        (ax, ay), (bx, by) = positions[edge.a], positions[edge.b]
        length = math.hypot(bx - ax, by - ay)
        dx, dy = (bx - ax) / length * 0.45, (by - ay) / length * 0.45
        key = f"{edge.a}_{edge.b}"
        nodes.append(Node(id=f"ge_{key}", style="secondary" if frozenset((edge.a, edge.b)) in on_path else "muted",
                          content=Curve(points=[[round(ax + dx, 4), round(ay + dy, 4)], [round(bx - dx, 4), round(by - dy, 4)]])))
        ids_for_edge = [f"ge_{key}"]
        if edge.weight is not None:
            nodes.append(Node(id=f"gw_{key}", content=Label(text=f"{edge.weight:g}", position=[
                round((ax + bx) / 2, 4), round((ay + by) / 2, 4)])))
            ids_for_edge.append(f"gw_{key}")
        focus[f"{edge.a}-{edge.b}"] = ids_for_edge
        everything += ids_for_edge
    focus["graph"] = everything
    return nodes


def diagram(**parts) -> Diagram:
    """Authoring helper: omitted parts are empty."""
    return Diagram(**{"axes": False, "sliders": [], "curves": [], "arrows": [], "points": [], "labels": [],
                      "graph": None, **parts})


def _beats(*pairs):
    return [DBeat(say=say, focus=list(focus)) for say, focus in pairs]


def _node(i, label):
    return DGraphNode(id=i, label=label)


def _edge(a, b, weight=None):
    return DGraphEdge(a=a, b=b, weight=weight)


# Authored diagram lessons for curriculum topics without a verified recipe. Every explanation
# in LUCID comes with a diagram; these also serve as worked examples for the author model.
AUTHORED = {
    "vectors": DiagramLesson(title="Vectors", diagram=diagram(
        axes=True, sliders=[DSlider(name="a", label="u x", min=-3, max=3, value=2),
                            DSlider(name="b", label="u y", min=-3, max=3, value=1)],
        arrows=[DArrow(id="u", start_x="0", start_y="0", end_x="a", end_y="b", style="primary"),
                DArrow(id="v", start_x="a", start_y="b", end_x="a+1", end_y="b+2", style="secondary"),
                DArrow(id="sum", start_x="0", start_y="0", end_x="a+1", end_y="b+2", style="muted")],
        labels=[DLabel(id="rule", text="u + v = (a + 1, b + 2)", x=0, y=-3.6)]),
        beats=_beats(("A vector is an arrow with a length and a direction. This one, u, is the list of numbers (a, b).", ["u"]),
                     ("To add v = (1, 2), place it at the tip of u. Vectors add tip to tail.", ["u", "v"]),
                     ("The sum runs from the start of u to the tip of v, adding component by component.", ["sum", "rule"]))),
    "vector_length": DiagramLesson(title="Vector length", diagram=diagram(
        axes=True, sliders=[DSlider(name="a", label="x component", min=-4, max=4, value=3),
                            DSlider(name="b", label="y component", min=-4, max=4, value=4)],
        arrows=[DArrow(id="v", start_x="0", start_y="0", end_x="a", end_y="b", style="primary")],
        curves=[DCurve(id="run", x="a*t", y="0", t_min=0, t_max=1, style="secondary"),
                DCurve(id="rise", x="a", y="b*t", t_min=0, t_max=1, style="secondary")],
        labels=[DLabel(id="rule", text="length = sqrt(a² + b²)", x=0, y=-3.6)]),
        beats=_beats(("The arrow and its two components form a right triangle.", ["v", "run", "rise"]),
                     ("By the Pythagorean theorem, the length is the square root of a squared plus b squared.", ["rule"]),
                     ("For (3, 4) that is the square root of 25, which is 5.", ["v"]))),
    "angles": DiagramLesson(title="Angles and cosine", diagram=diagram(
        axes=True, sliders=[DSlider(name="a", label="angle (degrees)", min=0, max=180, value=60)],
        curves=[DCurve(id="circle", x="3*cos(t)", y="3*sin(t)", t_min=0, t_max=3.1416, style="muted"),
                DCurve(id="shadow", x="3*cos(a*pi/180)*t", y="0", t_min=0, t_max=1, style="secondary")],
        arrows=[DArrow(id="arm", start_x="0", start_y="0", end_x="3*cos(a*pi/180)", end_y="3*sin(a*pi/180)", style="primary")],
        labels=[DLabel(id="rule", text="shadow = 3 cos(angle)", x=0, y=-2.4)]),
        beats=_beats(("The cosine of an angle is the horizontal shadow of a unit arrow at that angle. Here the arrow has length 3.", ["arm", "shadow"]),
                     ("At 0 degrees the shadow is the whole arrow, so cosine is 1. At 90 degrees there is no shadow, so cosine is 0.", ["shadow", "rule"]),
                     ("Past 90 degrees the shadow points backwards, and at 180 degrees cosine is minus 1.", ["arm", "circle"]))),
    "graphs": DiagramLesson(title="Graphs", diagram=diagram(graph=DGraph(
        nodes=[_node("a", "A"), _node("b", "B"), _node("c", "C"), _node("d", "D"), _node("f", "F")],
        edges=[_edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d"), _edge("d", "f")],
        highlight=["a", "c", "d", "f"])),
        beats=_beats(("A graph is a set of nodes joined by edges, like cities joined by roads.", ["graph"]),
                     ("A path follows edges from one node to another, like A to C to D to F.", ["a", "c", "d", "f"]))),
    "weighted_graphs": DiagramLesson(title="Weighted graphs", diagram=diagram(graph=DGraph(
        nodes=[_node("a", "A"), _node("b", "B"), _node("c", "C"), _node("d", "D")],
        edges=[_edge("a", "b", 2), _edge("b", "d", 5), _edge("a", "c", 4), _edge("c", "d", 1)],
        highlight=["a", "c", "d"])),
        beats=_beats(("In a weighted graph every edge carries a number, such as a distance or a cost.", ["graph"]),
                     ("A path costs the sum of its weights: A to B to D costs 2 plus 5, which is 7.", ["a-b", "b-d"]),
                     ("A to C to D costs 4 plus 1, which is 5, so it is the cheaper path even though it starts with a bigger edge.", ["a", "c", "d"]))),
    "bfs": DiagramLesson(title="Breadth-first search", diagram=diagram(graph=DGraph(
        nodes=[_node("s", "S · layer 0"), _node("a", "A · layer 1"), _node("b", "B · layer 1"),
               _node("c", "C · layer 2"), _node("d", "D · layer 2")],
        edges=[_edge("s", "a"), _edge("s", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d")],
        highlight=["s", "a", "c"])),
        beats=_beats(("Breadth-first search starts at S and visits every neighbour first: that is layer 1.", ["s", "a", "b"]),
                     ("Then it visits their unvisited neighbours, layer 2, and so on outward.", ["c", "d"]),
                     ("This finds paths with the fewest edges, but it ignores weights.", ["s", "a", "c"]))),
    "priority_queue": DiagramLesson(title="Priority queues", diagram=diagram(
        axes=True, points=[DPoint(id="two", x="-2", y="0", text="2 · out first"), DPoint(id="five", x="1", y="0", text="5"),
                           DPoint(id="nine", x="4", y="0", text="9")],
        labels=[DLabel(id="rule", text="smallest key comes out next", x=0, y=2.4)]),
        beats=_beats(("A priority queue holds items with keys, here 5, 2 and 9.", ["two", "five", "nine"]),
                     ("However they were added, the smallest key comes out next: 2, then 5, then 9.", ["two", "rule"]),
                     ("Dijkstra's algorithm uses one to pick the closest unsettled node.", ["rule"]))),
    "functions": DiagramLesson(title="Functions", diagram=diagram(
        axes=True, sliders=[DSlider(name="m", label="slope m", min=-2, max=2, value=1),
                            DSlider(name="c", label="intercept c", min=-3, max=3, value=1)],
        curves=[DCurve(id="f", x="t", y="m*t + c", t_min=-4, t_max=4, style="primary")],
        points=[DPoint(id="input", x="2", y="2*m + c", text="(2, f(2))")],
        labels=[DLabel(id="rule", text="f(x) = m x + c", x=-2.5, y=3.4)]),
        beats=_beats(("A function assigns each input x exactly one output f(x). This one is m times x plus c.", ["rule"]),
                     ("Its graph is every point (x, f(x)). At x = 2 the output is 2m plus c.", ["f", "input"]),
                     ("Move the sliders: each setting is a different function, but each input still has one output.", ["f"]))),
    "slope": DiagramLesson(title="Slope and secant lines", diagram=diagram(
        axes=True, sliders=[DSlider(name="p", label="first point", min=-3, max=3, value=-1),
                            DSlider(name="q", label="second point", min=-3, max=3, value=2)],
        curves=[DCurve(id="f", x="t", y="0.4*t^2", t_min=-3.2, t_max=3.2, style="primary"),
                DCurve(id="secant", x="p + (q - p)*t", y="0.4*p^2 + (0.4*q^2 - 0.4*p^2)*t", t_min=-0.3, t_max=1.3, style="secondary")],
        points=[DPoint(id="pp", x="p", y="0.4*p^2", text="p"), DPoint(id="qq", x="q", y="0.4*q^2", text="q")],
        labels=[DLabel(id="rule", text="slope = (f(q) − f(p)) / (q − p)", x=0, y=-2.4)]),
        beats=_beats(("The slope between two points is rise over run.", ["pp", "qq", "rule"]),
                     ("A secant line joins two points on a curve. Its slope is the average rate of change between them.", ["secant"]),
                     ("Slide the points closer together and watch the secant tilt.", ["secant", "pp", "qq"]))),
    "limits": DiagramLesson(title="Limits", diagram=diagram(
        axes=True, sliders=[DSlider(name="h", label="distance from 1", min=0.05, max=2, value=1)],
        curves=[DCurve(id="f", x="t", y="(t^2 - 1)/(t - 1)", t_min=-3, t_max=3, style="primary")],
        points=[DPoint(id="hole", x="1", y="2", text="limit 2 (hole)"), DPoint(id="near", x="1 + h", y="2 + h", text="x near 1")],
        labels=[DLabel(id="rule", text="f(x) = (x² − 1)/(x − 1)", x=-2.5, y=3.6)]),
        beats=_beats(("This function is not defined at x = 1: the formula would divide zero by zero.", ["rule", "hole"]),
                     ("But as x gets close to 1, f(x) gets close to 2. Slide the point in to see it.", ["near", "f"]),
                     ("That value, 2, is the limit. The function does not need to be defined at the point itself.", ["hole"]))),
    "continuity": DiagramLesson(title="Continuity", diagram=diagram(
        axes=True,
        curves=[DCurve(id="smooth", x="t", y="0.3*t^2 - 2", t_min=-4, t_max=4, style="primary"),
                DCurve(id="broken", x="t", y="1/t", t_min=-4, t_max=4, style="secondary")],
        labels=[DLabel(id="smooth_label", text="0.3x² − 2: continuous", x=2.6, y=3.6),
                DLabel(id="broken_label", text="1/x: breaks at 0", x=-2.6, y=3.6)]),
        beats=_beats(("A function is continuous where its graph has no breaks, jumps or holes.", ["smooth", "smooth_label"]),
                     ("One over x breaks at zero: you would have to lift your pen to draw it.", ["broken", "broken_label"]),
                     ("Polynomials like this parabola are continuous everywhere.", ["smooth"]))),
    "derivatives": DiagramLesson(title="Derivatives", diagram=diagram(
        axes=True, sliders=[DSlider(name="p", label="point x", min=-2.8, max=2.8, value=1)],
        curves=[DCurve(id="f", x="t", y="0.25*t^3 - t", t_min=-3, t_max=3, style="primary"),
                DCurve(id="tangent", x="p + t", y="0.25*p^3 - p + (0.75*p^2 - 1)*t", t_min=-1.2, t_max=1.2, style="secondary")],
        points=[DPoint(id="at", x="p", y="0.25*p^3 - p", text="f′(p) = 0.75p² − 1")],
        labels=[DLabel(id="rule", text="f(x) = 0.25x³ − x", x=-2.5, y=3.6)]),
        beats=_beats(("The derivative is the slope of the tangent line, the line that just touches the curve.", ["tangent", "at"]),
                     ("It is the limit of secant slopes as the two points merge into one.", ["tangent"]),
                     ("Slide the point: where the curve is flat, the derivative is zero.", ["at", "f"]))),
}
