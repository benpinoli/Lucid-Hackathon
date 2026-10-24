"""Deterministic teaching recipes and verified mathematics.

Each recipe is a builder over typed parameters. Builders own every number shown or spoken:
callers (the keyword router or the Claude planner) only choose parameters, and builders
reject anything outside the viewport, the scene budget or the mathematics' domain.
"""
import heapq
from math import atan2, cos, degrees, fsum, hypot, pi, sin, sqrt

from .scene import Arrow, Axes, Curve, Label, MAX_OBJECTS, Node, Slider, StrictModel
from pydantic import Field


class LessonError(ValueError):
    """Parameters a recipe cannot teach; the message is safe to show a planner model."""


def projection(v: list[float], u: list[float]) -> list[float]:
    norm2 = fsum(x * x for x in u)
    if norm2 == 0:
        raise ValueError("Cannot project onto the zero vector")
    factor = fsum(a * b for a, b in zip(v, u, strict=True)) / norm2
    return [factor * x for x in u]


def dijkstra(edges, source):
    """Distances, predecessors, settle order and per-settle relaxations on an undirected graph.

    Each trace entry is (node, distance, [(neighbour, old or None, new, weight)]).
    """
    adjacent = {}
    for a, b, weight in edges:
        if weight < 0:
            raise ValueError("Dijkstra requires nonnegative weights")
        adjacent.setdefault(a, []).append((b, weight))
        adjacent.setdefault(b, []).append((a, weight))
    if source not in adjacent:
        raise ValueError("Unknown source")
    distance, previous, order, trace, settled = {source: 0}, {}, [], [], set()
    heap = [(0, source)]
    while heap:
        current, u = heapq.heappop(heap)
        if u in settled:
            continue
        settled.add(u)
        order.append(u)
        updates = []
        for v, weight in sorted(adjacent[u]):
            if v not in distance or current + weight < distance[v]:
                updates.append((v, distance.get(v), current + weight, weight))
                distance[v] = current + weight
                previous[v] = u
                heapq.heappush(heap, (distance[v], v))
        trace.append((u, current, updates))
    return distance, previous, order, trace


def path_to(previous, source, target):
    path = [target]
    while path[-1] != source:
        if path[-1] not in previous:
            raise ValueError("Target is unreachable")
        path.append(previous[path[-1]])
    return path[::-1]


def polynomial(coefficients, x):
    return fsum(c * x ** power for power, c in enumerate(coefficients))


def mean_value_points(coefficients, a, b):
    """Exact points c in (a, b) with f'(c) = (f(b) - f(a)) / (b - a) for a cubic or lower.

    Returns (points, slope); points is empty when f is linear, where every c works.
    """
    c0, c1, c2, c3 = coefficients
    slope = (polynomial(coefficients, b) - polynomial(coefficients, a)) / (b - a)
    # f'(x) - slope = 3*c3*x^2 + 2*c2*x + (c1 - slope)
    qa, qb, qc = 3 * c3, 2 * c2, c1 - slope
    scale = max(abs(qa), abs(qb), abs(qc), 1.0)
    if abs(qa) < 1e-12 * scale and abs(qb) < 1e-12 * scale:
        return [], slope
    if abs(qa) < 1e-12 * scale:
        roots = [-qc / qb]
    else:
        disc = max(qb * qb - 4 * qa * qc, 0.0)
        # Numerically stable quadratic roots.
        q = -(qb + (sqrt(disc) if qb >= 0 else -sqrt(disc))) / 2
        roots = [q / qa] + ([qc / q] if q != 0 else [])
    tolerance = 1e-9 * (b - a)
    points = sorted({round(r, 12) for r in roots if a + tolerance < r < b - tolerance})
    if not points:
        raise LessonError("No mean value point found; the theorem's hypotheses may be violated")
    return points, slope


class TeachingBeat(StrictModel):
    beat_id: str
    say_text: str
    focus_ids: list[str]
    cue: str = "immediate"
    allow_interrupt: bool = True


class LessonPlan(StrictModel):
    schema_version: int = 1
    recipe_id: str
    beats: list[TeachingBeat]
    comprehension_prompt: str = "How well do you understand this concept, from 1 to 10?"
    graph_context: dict | None = None
    # Who chose the example; every number is computed by these builders either way.
    planned_by: str = "authored"
    # Curriculum concept this lesson teaches, so a self-rating can update the learner's graph.
    concept_id: str | None = None
    # True when an author model wrote the explanation; geometry is still computed by LUCID.
    generated: bool = False
    # Tutor state for clients and the voice model: {"mode": ..., "awaiting_answer": bool}.
    tutor: dict | None = None
    nodes: list[Node] = Field(exclude=True)


def _points(points):
    # Bounded precision keeps curve-heavy scenes well inside the 16 KiB event budget.
    return [[round(x, 4), round(y, 4)] for x, y in points]


def _num(x, places=3):
    """Speakable number: few decimals, no trailing zeros, no negative zero."""
    value = round(x, places) + 0.0
    return f"{value:g}"


def _vec(v):
    return f"({_num(v[0])}, {_num(v[1])})"


def _and(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _check(condition, message):
    if not condition:
        raise LessonError(message)


VIEW = 4.5  # Board units visible around the origin with the axes2d extent of 5.


def vector_projection(u=(3.0, 0.0), v=(2.0, 3.0)) -> LessonPlan:
    u, v = [float(x) for x in u], [float(x) for x in v]
    _check(len(u) == 2 and len(v) == 2, "u and v must be 2D vectors")
    _check(all(abs(x) <= VIEW for x in u + v), f"Vector components must be between -{VIEW} and {VIEW}")
    _check(hypot(*u) >= 0.5 and hypot(*v) >= 0.5, "u and v must each have length at least 0.5")
    shadow = projection(v, u)
    dot, uu = fsum(a * b for a, b in zip(u, v)), fsum(a * a for a in u)
    return LessonPlan(recipe_id="vector_projection_v1", nodes=[
        Node(id="axes", content=Axes()),
        Node(id="u", content=Arrow(end=u)),
        Node(id="v", content=Arrow(end=v), style="secondary"),
        Node(id="projection", content=Arrow(end=shadow)),
        Node(id="formula", content=Label(text="proj_u(v) = (u dot v / u dot u) u")),
        Node(id="angle", content=Slider(binding="projection.angle", minimum=-180.0,
                                        maximum=180.0, value=round(degrees(atan2(v[1], v[0])), 6))),
    ], beats=[
        TeachingBeat(beat_id="vectors", say_text=f"The two arrows represent u = {_vec(u)} "
                     f"and v = {_vec(v)}.", focus_ids=["u", "v"]),
        TeachingBeat(beat_id="projection", say_text=f"Project v onto u: its shadow is {_vec(shadow)}.",
                     focus_ids=["projection"]),
        TeachingBeat(beat_id="dot", say_text=f"Here u dot v is {_num(dot)}, and u dot u is {_num(uu)}.",
                     focus_ids=["formula"]),
    ])


GRAPH_POSITIONS = {"A": (-4.5, 0.0), "B": (-1.5, 2.5), "C": (-1.5, -2.5),
                   "D": (1.5, 2.5), "E": (4.5, 0.0)}
GRAPH_EDGES = [("A", "B", 4), ("A", "C", 2), ("B", "C", 1), ("B", "D", 5),
               ("C", "D", 8), ("C", "E", 10), ("D", "E", 3)]
NODE_RADIUS = 0.5


def _layout(names, source):
    """Ellipse layout with the source on the left, in the learner's node order."""
    start = names.index(source)
    ordered = names[start:] + names[:start]
    return {name: (round(4.5 * cos(pi - 2 * pi * i / len(ordered)), 4),
                   round(2.6 * sin(pi - 2 * pi * i / len(ordered)), 4))
            for i, name in enumerate(ordered)}


def dijkstra_lesson(nodes=tuple(GRAPH_POSITIONS), edges=tuple(GRAPH_EDGES), source="A",
                    target="E", positions=None) -> LessonPlan:
    names = list(nodes)
    _check(2 <= len(names) <= 7, "Use between 2 and 7 nodes")
    _check(len(set(names)) == len(names), "Node names must be unique")
    _check(all(1 <= len(n) <= 3 and n.isascii() and n.isalnum() for n in names),
           "Node names must be 1-3 letters or digits")
    edges = [(a, b, w) for a, b, w in edges]
    _check(1 <= len(edges) <= 12, "Use between 1 and 12 edges")
    _check(all(a in names and b in names and a != b for a, b, _ in edges),
           "Every edge must join two different listed nodes")
    _check(len({frozenset((a, b)) for a, b, _ in edges}) == len(edges), "Each pair of nodes may have one edge")
    _check(all(isinstance(w, int) and 1 <= w <= 99 for _, _, w in edges),
           "Edge weights must be whole numbers from 1 to 99")
    _check(2 * len(names) + 2 * len(edges) + 1 <= MAX_OBJECTS,
           f"Too many objects: need 2 x nodes + 2 x edges + 1 <= {MAX_OBJECTS}")
    _check(source in names and target in names and source != target,
           "Source and target must be two different listed nodes")
    distance, previous, _, trace = dijkstra(edges, source)
    _check(target in distance, f"{target} is unreachable from {source}")
    route = path_to(previous, source, target)
    on_route = {frozenset(pair) for pair in zip(route, route[1:])}
    positions = positions or _layout(names, source)
    weight_of = {frozenset((a, b)): (a, b, w) for a, b, w in edges}
    edge_id = {key: f"edge_{a}_{b}" for key, (a, b, _) in weight_of.items()}

    scene, placed = [], []
    for name in names:
        x, y = positions[name]
        circle = [(x + NODE_RADIUS * cos(2 * pi * i / 16), y + NODE_RADIUS * sin(2 * pi * i / 16))
                  for i in range(17)]
        scene.append(Node(id=f"node_{name}", content=Curve(points=_points(circle))))
        label_at = (x, y + (0.95 if y >= 0 else -0.95))
        scene.append(Node(id=f"label_{name}", content=Label(
            text=f"{name}: {distance.get(name, '∞')}", position=list(label_at))))
        placed += [(x, y), label_at]
    for key, (a, b, weight) in weight_of.items():
        (ax, ay), (bx, by) = positions[a], positions[b]
        length = hypot(bx - ax, by - ay)
        dx, dy = (bx - ax) / length, (by - ay) / length
        # Edges stop at circle boundaries so node outlines stay legible.
        scene.append(Node(id=edge_id[key], style="secondary" if key in on_route else "muted",
                          content=Curve(points=_points([
                              (ax + dx * NODE_RADIUS, ay + dy * NODE_RADIUS),
                              (bx - dx * NODE_RADIUS, by - dy * NODE_RADIUS)]))))
        # Slide the weight along its edge until it clears earlier labels (chords can share midpoints).
        spots = [(ax + (bx - ax) * t, ay + (by - ay) * t) for t in (0.5, 0.35, 0.65, 0.25, 0.75)]
        spot = next((s for s in spots if all(hypot(s[0] - p[0], s[1] - p[1]) >= 0.9 for p in placed)),
                    spots[0])
        placed.append(spot)
        scene.append(Node(id=f"w_{a}_{b}", content=Label(text=str(weight), position=list(spot))))
    # Not id "formula": the renderer pins that ID onto the caption row.
    scene.append(Node(id="relax_rule", content=Label(text="dist(v) = min(dist(v), dist(u) + w(u, v))",
                                                     position=[0.0, 4.4])))

    beats = [TeachingBeat(beat_id="start", say_text=f"Start at {source} with distance 0. Every other "
                          "node starts at infinity because we have not reached it yet.",
                          focus_ids=[f"node_{source}", f"label_{source}"])]
    for u, d, updates in trace:
        if u == target:
            break
        phrases = [f"{v} gets {d} + {w} = {new}" if old is None else
                   f"{v} improves from {old} to {d} + {w} = {new}" for v, old, new, w in updates]
        if u == source:
            text = f"Settle {source} and relax its edges: {_and(phrases)}."
        else:
            text = f"{u} has the smallest tentative distance, {d}, so it is final. " + (
                f"Through {u}, {_and(phrases)}." if phrases else "None of its edges gives a shorter route.")
        focus = [f"node_{u}", f"label_{u}"]
        for v, *_ in updates:
            key = frozenset((u, v))
            a, b, _ = weight_of[key]
            focus += [edge_id[key], f"w_{a}_{b}", f"label_{v}"]
        if u == source:
            focus.append("relax_rule")
        beats.append(TeachingBeat(beat_id=f"settle_{u}", say_text=text, focus_ids=focus))
    beats.append(TeachingBeat(beat_id="finish", say_text=f"{target} is settled at distance "
                              f"{distance[target]}. Shortest path: {' → '.join(route)}.",
                              focus_ids=[edge_id[key] for key in on_route]
                              + [f"label_{name}" for name in route]))
    return LessonPlan(recipe_id="dijkstra_v1", nodes=scene, beats=beats)


def _poly_text(coefficients):
    terms = []
    for power in (3, 2, 1, 0):
        c = round(coefficients[power], 4)
        if c == 0:
            continue
        magnitude = "" if abs(c) == 1 and power else _num(abs(c))
        body = magnitude + {3: "x³", 2: "x²", 1: "x", 0: ""}[power]
        terms.append(("− " if c < 0 else "+ ") + body)
    if not terms:
        return "0"
    text = " ".join(terms)
    return text[2:] if text.startswith("+ ") else "−" + text[2:]


CURVE_LIMIT = 3.8  # Keeps the curve below the formula label at the top of the board.


def _segment(p, q, steps=12):
    return [(p[0] + (q[0] - p[0]) * i / steps, p[1] + (q[1] - p[1]) * i / steps) for i in range(steps + 1)]


def _place(text, candidates, obstacles):
    """First candidate whose label box clears every obstacle point, else the clearest one.

    Labels are opaque plates (see renderer text_label), so a plate over a line hides it.
    The box approximates the renderer's fitted width for a 0.7-unit-high label.
    """
    half_width, half_height = 0.12 * len(text) + 0.13, 0.35

    def clearance(spot):
        return min(max(abs(spot[0] - x) - half_width, abs(spot[1] - y) - half_height) for x, y in obstacles)

    spot = next((c for c in candidates if clearance(c) >= 0.1), max(candidates, key=clearance))
    # Later labels must also avoid this plate.
    obstacles += [(spot[0] + dx * half_width, spot[1] + dy * half_height)
                  for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    return list(spot)


def mean_value_lesson(coefficients=(0.0, 0.0, 0.0, 0.125), a=0.0, b=3.0) -> LessonPlan:
    coefficients = [float(c) for c in coefficients]
    _check(len(coefficients) == 4, "Give exactly four coefficients [c0, c1, c2, c3]")
    _check(all(abs(c) <= 10 for c in coefficients), "Coefficients must be between -10 and 10")
    a, b = float(a), float(b)
    _check(-VIEW <= a < b <= VIEW and b - a >= 0.5,
           f"Need -{VIEW} <= a < b <= {VIEW} with b - a at least 0.5")

    def f(x):
        return polynomial(coefficients, x)

    _check(all(abs(f(a + (b - a) * i / 63)) <= CURVE_LIMIT for i in range(64)),
           f"f must stay between -{CURVE_LIMIT} and {CURVE_LIMIT} on [a, b] to fit the view")
    points, slope = mean_value_points(coefficients, a, b)
    linear = not points
    shown = points or [(a + b) / 2]

    # Extend the plot beyond [a, b] while it stays in view.
    low, high = a, b
    for _ in range(20):
        if low - 0.05 >= -4.8 and abs(f(low - 0.05)) <= CURVE_LIMIT + 0.1:
            low -= 0.05
        if high + 0.05 <= 4.8 and abs(f(high + 0.05)) <= CURVE_LIMIT + 0.1:
            high += 0.05
    curve = [(low + (high - low) * i / 63, f(low + (high - low) * i / 63)) for i in range(64)]
    poly = _poly_text(coefficients)
    reach = 2.0 / hypot(1, slope)  # Tangent segments have the same on-screen length at any slope.
    tangents = [[(c - reach, f(c) - reach * slope), (c + reach, f(c) + reach * slope)] for c in shown]
    obstacles = curve + _segment((a, f(a)), (b, f(b)))
    for tangent, c in zip(tangents, shown):
        obstacles += _segment(*tangent) + _segment((c, 0.0), (c, f(c)))
    offsets = (-0.6, 0.6, -1.4, 1.4, -2.2, 2.2)
    a_text, b_text = f"a = {_num(a)}", f"b = {_num(b)}"
    a_at = _place(a_text, [(a, y) for y in offsets], obstacles)
    b_at = _place(b_text, [(b, y) for y in offsets], obstacles)
    c_texts = ["any c"] if linear else [f"c ≈ {_num(c, 2)}" for c in shown]
    c_at = [_place(text, [(c, y) for y in offsets], obstacles) for text, c in zip(c_texts, shown)]
    f_text = f"f(x) = {poly}"
    f_at = _place(f_text, [(-3.2, 3.4), (3.2, 3.4), (-3.2, -3.2), (3.2, -3.2)], obstacles)

    scene = [
        Node(id="axes", content=Axes()),
        Node(id="f", content=Curve(points=_points(curve))),
        Node(id="f_label", content=Label(text=f_text, position=f_at)),
        Node(id="secant", style="secondary", content=Curve(points=_points([(a, f(a)), (b, f(b))]))),
        Node(id="a_label", content=Label(text=a_text, position=a_at)),
        Node(id="b_label", content=Label(text=b_text, position=b_at)),
        Node(id="mvt_formula", content=Label(text="f′(c) = (f(b) − f(a)) / (b − a)", position=[-1.0, 4.4])),
    ]
    tangent_ids = []
    for i, (c, tangent, text, at) in enumerate(zip(shown, tangents, c_texts, c_at), start=1):
        scene += [
            Node(id=f"tangent_{i}", style="secondary", content=Curve(points=_points(tangent))),
            Node(id=f"c_drop_{i}", style="muted", content=Curve(points=_points([(c, 0.0), (c, f(c))]))),
            Node(id=f"c_label_{i}", content=Label(text=text, position=at)),
        ]
        tangent_ids += [f"tangent_{i}", f"c_drop_{i}", f"c_label_{i}"]

    if linear:
        tangent_text = (f"f is a straight line, so every c between {_num(a)} and {_num(b)} works: "
                        "the tangent is the line itself, parallel to the secant.")
    elif len(points) == 1:
        tangent_text = (f"At c ≈ {_num(points[0], 2)}, the tangent slope f′(c) is also {_num(slope)}, "
                        "so the tangent runs parallel to the secant.")
    else:
        tangent_text = (f"Two points work, c ≈ {_num(points[0], 2)} and c ≈ {_num(points[1], 2)}. At each, "
                        f"the tangent slope f′(c) is {_num(slope)}, parallel to the secant.")
    return LessonPlan(recipe_id="mean_value_v1", nodes=scene, beats=[
        TeachingBeat(beat_id="hypotheses", say_text=f"f(x) = {poly} is a polynomial, so it is "
                     f"continuous on [{_num(a)}, {_num(b)}] and differentiable on ({_num(a)}, {_num(b)}). "
                     "The theorem applies.", focus_ids=["f", "f_label", "a_label", "b_label"]),
        TeachingBeat(beat_id="secant", say_text=f"The secant joins ({_num(a)}, {_num(f(a))}) and "
                     f"({_num(b)}, {_num(f(b))}). Its slope is {_num(slope)}, the average rate of change.",
                     focus_ids=["secant", "mvt_formula"]),
        TeachingBeat(beat_id="tangent", say_text=tangent_text, focus_ids=tangent_ids),
    ])


def whiteboard() -> LessonPlan:
    return LessonPlan(recipe_id="whiteboard_v1", nodes=[
        Node(id="notice", content=Label(text="No verified animation for this topic yet."))
    ], beats=[TeachingBeat(beat_id="fallback", say_text="Try asking about dot product, "
                           "Dijkstra's algorithm, or the mean value theorem.",
                           focus_ids=["notice"])])


def authored_dijkstra() -> LessonPlan:
    return dijkstra_lesson(positions=GRAPH_POSITIONS)


# First match wins. "dijk" also catches common misspellings such as "dijktra".
RECIPES = (
    (("dot product", "projection", "vector"), vector_projection),
    (("dijk", "shortest path"), authored_dijkstra),
    (("mean value", "mvt"), mean_value_lesson),
)


def plan(text: str) -> LessonPlan:
    """Keyword router over the authored default examples; needs no provider."""
    query = text.casefold()
    for keywords, build in RECIPES:
        if any(word in query for word in keywords):
            return build()
    return whiteboard()
