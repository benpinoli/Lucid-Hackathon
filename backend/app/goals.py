"""Learning goals: curriculum in the God Brain, diagnostic evaluation, cheapest routes.

The memory graph stays the source of truth. The curriculum is synced into the God Brain;
a diagnostic quiz records real understanding assessments in the learner's Human Brain;
routes are the engine's baseline Dijkstra over that personal graph, from the concepts the
learner knows to the target. Scores are never invented: every membership and edge comes
from a quiz answer, a lesson self-rating, or the documented derived-familiarity rule.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from memory_graph import NotFound, shortest_path

from .learning import LessonPlan, TeachingBeat, plan as authored
from .scene import Curve, Label, MAX_OBJECTS, Node

KNOWN = 7.0  # Understanding at or above this counts as already known.
QUIZ_SCORES = {"correct": 7.0, "wrong": 2.0, "unsure": 1.0}
QUIZ_CONFIDENCE = 0.6  # One question is weak evidence; stored as metadata, never in the cost.
REGION_LIMIT = 8
ROW = 2.4  # Off-route rows on the route map; labels stay clear of the caption row.
PROVENANCE = {"source": "lucid_curriculum_v1"}


@dataclass(frozen=True)
class Topic:
    key: str
    name: str
    short: str  # Map label; keeps route scenes legible.
    level: int  # 0 = foundation: where a learner with no known concepts starts.
    description: str  # Authored concept-card text, two or three sentences.
    question: str
    choices: tuple[str, ...]
    answer: int
    aliases: tuple[str, ...] = ()
    recipe: str | None = None  # Request text for a verified visual recipe.


TOPICS = (
    Topic("vectors", "Vectors", "Vectors", 0,
          "A vector is an arrow with a length and a direction, written as a list of numbers such as "
          "(3, 0). Vectors add tip to tail, component by component.",
          "What is (1, 2) + (3, 4)?", ("(4, 6)", "(3, 8)", "(2, 2)", "(4, 2)"), 0, ("vector",)),
    Topic("vector_length", "Vector length", "Length", 1,
          "The length of a vector (a, b) is the square root of a squared plus b squared. "
          "It comes straight from the Pythagorean theorem.",
          "What is the length of (3, 4)?", ("7", "5", "12", "25"), 1, ("magnitude", "norm")),
    Topic("angles", "Angles and cosine", "Cosine", 1,
          "The cosine of an angle is the horizontal shadow of a unit arrow at that angle. "
          "It is 1 when arrows agree, 0 when they are perpendicular and -1 when they are opposite.",
          "What is cos(90°)?", ("0", "1", "-1", "0.5"), 0, ("cosine", "angle between")),
    Topic("dot_product", "Dot product", "Dot product", 2,
          "The dot product multiplies matching components and adds them: (a, b) dot (c, d) is ac + bd. "
          "It measures how much two vectors point the same way.",
          "What is (1, 2) dot (3, 4)?", ("11", "10", "(3, 8)", "7"), 0, ("dot product",), "dot product"),
    Topic("vector_projection", "Vector projection", "Projection", 3,
          "Projecting v onto u gives the shadow of v along u. "
          "Its length is the dot product divided by the length of u.",
          "Projecting v onto u gives…", ("the shadow of v along u", "a vector perpendicular to u",
                                         "the length of u", "u + v"), 0, ("projection",), "vector projection"),
    Topic("graphs", "Graphs", "Graphs", 0,
          "A graph is a set of nodes joined by edges, like cities joined by roads. "
          "A path is a sequence of edges from one node to another.",
          "In a graph, an edge connects…", ("two nodes", "two graphs", "a node to a weight", "nothing"), 0,
          ("graph theory", "nodes and edges")),
    Topic("weighted_graphs", "Weighted graphs", "Weights", 1,
          "In a weighted graph every edge carries a number, such as a distance or a cost. "
          "The cost of a path is the sum of its edge weights.",
          "A path uses edges of weight 2 and 5. What does it cost?", ("7", "10", "3", "5"), 0,
          ("weighted graph", "edge weight")),
    Topic("bfs", "Breadth-first search", "BFS", 2,
          "Breadth-first search explores a graph layer by layer from a start node. "
          "It finds paths with the fewest edges, but ignores weights.",
          "Breadth-first search finds the path with the fewest…", ("edges", "total weight", "cycles", "nodes"), 0,
          ("breadth-first", "breadth first", "bfs")),
    Topic("priority_queue", "Priority queues", "Priority queue", 2,
          "A priority queue always hands back the item with the smallest key next. "
          "Dijkstra's algorithm uses one to pick the closest unsettled node.",
          "A min-priority queue holds 5, 2 and 9. What comes out first?", ("2", "5", "9", "whichever was added first"),
          0, ("priority queue", "heap")),
    Topic("dijkstra", "Dijkstra's algorithm", "Dijkstra", 3,
          "Dijkstra's algorithm finds the cheapest paths from a start node when every weight is nonnegative. "
          "Each round it settles the closest unsettled node and relaxes its edges.",
          "Dijkstra's algorithm requires edge weights that are…", ("nonnegative", "whole numbers", "all equal",
                                                                    "negative"), 0,
          ("dijk", "shortest path"), "Dijkstra's algorithm"),
    Topic("functions", "Functions", "Functions", 0,
          "A function assigns each input x exactly one output f(x). "
          "Its graph is the curve of points (x, f(x)).",
          "If f(x) = 2x + 1, what is f(3)?", ("7", "6", "9", "5"), 0, ("function",)),
    Topic("slope", "Slope and secant lines", "Slope", 1,
          "The slope between two points is rise over run. "
          "A secant line joins two points on a curve, and its slope is the average rate of change.",
          "What is the slope from (1, 2) to (3, 8)?", ("3", "6", "2", "4"), 0, ("secant", "rate of change")),
    Topic("limits", "Limits", "Limits", 1,
          "A limit describes the value f(x) approaches as x gets close to a point. "
          "The function does not even need to be defined at that point.",
          "As x approaches 2, x² approaches…", ("4", "2", "0", "infinity"), 0, ("limit",)),
    Topic("continuity", "Continuity", "Continuity", 2,
          "A function is continuous where its graph has no breaks, jumps or holes. "
          "Polynomials are continuous everywhere.",
          "Which function is continuous everywhere?", ("x²", "1/x", "tan x", "the floor function"), 0,
          ("continuous",)),
    Topic("derivatives", "Derivatives", "Derivatives", 2,
          "The derivative f′(x) is the slope of the tangent line at x. "
          "It is the limit of secant slopes as the two points merge.",
          "What is the derivative of x²?", ("2x", "x", "x³/3", "2"), 0, ("derivative", "tangent", "differentiat")),
    Topic("mean_value_theorem", "Mean value theorem", "MVT", 3,
          "If f is continuous on [a, b] and differentiable inside it, some point c has a tangent "
          "parallel to the secant. There, the instant rate of change equals the average rate.",
          "The mean value theorem guarantees a point where the tangent is…",
          ("parallel to the secant", "always horizontal", "vertical", "perpendicular to the secant"), 0,
          ("mean value", "mvt"), "mean value theorem"),
)

# Similarity 1-10 is the God Brain's inherent relatedness; the route formula rewards it.
# A link must mean "directly builds on": the baseline cost 1 / (s * (u_a + u_b)) makes even a
# weak link cheap once one end is well understood, so a loosely related shortcut (for example
# slope -> mean value theorem) would let a route skip a real prerequisite such as derivatives.
LINKS = (
    ("vectors", "vector_length", 8), ("vectors", "dot_product", 8), ("vector_length", "angles", 6),
    ("angles", "dot_product", 7), ("vector_length", "dot_product", 6), ("dot_product", "vector_projection", 9),
    ("graphs", "weighted_graphs", 9), ("graphs", "bfs", 7), ("weighted_graphs", "dijkstra", 8),
    ("bfs", "dijkstra", 7), ("priority_queue", "dijkstra", 8), ("weighted_graphs", "priority_queue", 3),
    ("functions", "slope", 8), ("functions", "limits", 6), ("limits", "continuity", 8),
    ("limits", "derivatives", 8), ("slope", "derivatives", 9), ("continuity", "mean_value_theorem", 7),
    ("derivatives", "mean_value_theorem", 9),
)

# The demo seed predates the curriculum; keep its concept IDs.
STABLE_IDS = {"vectors": str(UUID(int=1)), "dot_product": str(UUID(int=2))}


class GoalError(NotFound):
    """Unknown goal topic; carries the topics a learner can choose instead."""


class Curriculum:
    """Authored topics mapped onto God Brain concept IDs."""

    def __init__(self, topics=TOPICS, links=LINKS):
        self.topics, self.links = topics, links
        self.ids: dict[str, str] = {}
        self.by_id: dict[str, Topic] = {}

    def sync(self, graph):
        """Additive, idempotent: reuse concepts by stable ID or exact name, never edit scores."""
        with graph.transaction():
            for topic in self.topics:
                concept_id = STABLE_IDS.get(topic.key, str(uuid5(NAMESPACE_URL, f"lucid:{topic.key}")))
                try:
                    concept_id = graph.god.get_concept(concept_id).id
                except NotFound:
                    try:
                        concept_id = graph.god.get_concept(topic.name).id
                    except NotFound:
                        graph.god.add_concept(topic.name, description=topic.description, concept_id=concept_id)
                self.ids[topic.key] = concept_id
                self.by_id[concept_id] = topic
            for a, b, similarity in self.links:
                a_id, b_id = self.ids[a], self.ids[b]
                if b_id not in graph.god.neighbors(a_id):
                    graph.god.connect(a_id, b_id, similarity=similarity, provenance=PROVENANCE)
        return self

    def topic(self, concept_id) -> Topic:
        if concept_id not in self.by_id:
            raise NotFound("Concept is not part of the curriculum")
        return self.by_id[concept_id]

    def match(self, text) -> str | None:
        """Longest alias or name contained in the request wins; None when nothing matches."""
        query = text.casefold()
        best = max(((len(alias), self.ids[t.key]) for t in self.topics
                    for alias in (t.name.casefold(), *t.aliases) if alias in query), default=None)
        return best[1] if best else None

    def listing(self):
        return [{"id": self.ids[t.key], "name": t.name, "level": t.level, "has_lesson": bool(t.recipe)}
                for t in self.topics]


def _name(snapshot, concept_id, curriculum):
    if concept_id in curriculum.by_id:
        return curriculum.by_id[concept_id].name
    return snapshot.nodes[concept_id].label


class Goals:
    """Goal setting, diagnosis, routing and progress for one MemoryGraph."""

    def __init__(self, graph, curriculum: Curriculum, planner, resolver=None):
        self.graph, self.curriculum, self.planner = graph, curriculum, planner
        self.resolver = resolver  # Optional text -> concept ID (Claude); aliases are tried first.

    # ---- reading -------------------------------------------------------------------------
    def resolve(self, text) -> str:
        concept_id = self.curriculum.match(text)
        if concept_id is None and self.resolver is not None:
            concept_id = self.resolver(text)
        if concept_id is None:
            raise GoalError("That topic is not in the curriculum yet. Try: "
                            + ", ".join(t.name for t in self.curriculum.topics if t.level >= 2) + ".")
        return concept_id

    def region(self, target):
        """Curriculum concepts nearest the target in the God Brain, the target first."""
        seen, queue = [target], deque([target])
        while queue and len(seen) < REGION_LIMIT:
            for neighbor in self.graph.god.neighbors(queue.popleft()):
                if neighbor in self.curriculum.by_id and neighbor not in seen and len(seen) < REGION_LIMIT:
                    seen.append(neighbor)
                    queue.append(neighbor)
        return seen

    def questions(self, user, target):
        members = self.graph.human(user).snapshot().nodes
        # Foundations first, so the check reads from easy to hard.
        fresh = sorted((c for c in self.region(target) if c not in members),
                       key=lambda c: self.curriculum.topic(c).level)
        return [{"concept_id": c, "name": topic.name, "prompt": topic.question, "choices": list(topic.choices)}
                for c in fresh for topic in [self.curriculum.topic(c)]]

    def start(self, user, text):
        target = self.resolve(text)
        questions = self.questions(user, target)
        return {"target": {"id": target, "name": self.curriculum.topic(target).name},
                "questions": questions, "revision": self.graph.human(user).revision,
                "route": None if questions else self.route(user, target)}

    def route(self, user, target):
        snapshot = self.graph.human(user).snapshot()
        topic = self.curriculum.topic(target)
        base = {"target": {"id": target, "name": topic.name}, "revision": snapshot.revision.human,
                "formula_id": snapshot.methodology.formula_id}
        if target not in snapshot.nodes:
            return {**base, "status": "needs_diagnostic", "steps": [], "next": None, "total_cost": None}

        def known(concept_id):
            return snapshot.nodes[concept_id].understanding >= KNOWN

        result = None
        if known(target):
            concepts, status, cost = [target], "known", 0.0
        else:
            region = set(self.region(target))
            starts = [[c for c in snapshot.nodes if c != target and known(c)],
                      # A learner who knows nothing nearby starts from the cluster's foundations.
                      [c for c in region if c != target and c in snapshot.nodes
                       and self.curriculum.by_id[c].level == 0]]
            for sources in starts:
                if sources:
                    result = shortest_path(snapshot, sources, target)
                    if result.status == "reachable":
                        break
            if result is not None and result.status == "reachable":
                concepts, status, cost = list(result.concepts), "route", result.total_cost
            else:
                concepts, status, cost = [target], "route", None
        steps = [{"id": c, "name": _name(snapshot, c, self.curriculum),
                  "understanding": snapshot.nodes[c].understanding, "known": known(c),
                  "has_lesson": bool(self.curriculum.by_id.get(c) and self.curriculum.by_id[c].recipe)}
                 for c in concepts]
        upcoming = [s["id"] for s in steps if not s["known"]]
        return {**base, "status": status, "steps": steps, "next": upcoming[0] if upcoming else None,
                "total_cost": cost}

    # ---- writing -------------------------------------------------------------------------
    def _link(self, human, concept_ids):
        """Personal edges for God edges between members, so routes can traverse them.

        Familiarity is not in the baseline cost; the initial value is the documented rule
        min(endpoint understanding), recorded with method "derived_min_understanding".
        """
        snapshot = human.snapshot()
        for concept_id in concept_ids:
            for neighbor in self.graph.god.neighbors(concept_id):
                # Human snapshots hold personal edges only; God edges come from neighbors().
                if neighbor in snapshot.nodes and tuple(sorted((concept_id, neighbor))) not in snapshot.edges:
                    familiarity = min(snapshot.nodes[concept_id].understanding, snapshot.nodes[neighbor].understanding)
                    human.connect(concept_id, neighbor, familiarity=familiarity, method="derived_min_understanding",
                                  provenance={"rule": "min endpoint understanding at first link"})
                    snapshot = human.snapshot()

    def record(self, user, concept_id, score, method, provenance, previous=None):
        """One graded observation: add or update the concept, link it, rate the edge just crossed."""
        human = self.graph.human(user)
        with self.graph.transaction():
            snapshot = human.snapshot()
            if concept_id in snapshot.nodes:
                human.assess_understanding(concept_id, score, method=method, provenance=provenance)
            else:
                human.add_concept(concept_id, understanding=score, method=method, provenance=provenance)
            self._link(human, [concept_id])
            edge = tuple(sorted((concept_id, previous))) if previous else None
            if edge in human.snapshot().edges:
                human.assess_familiarity(previous, concept_id, score, method=method, provenance=provenance)

    def diagnose(self, user, target, answers, expected_revision, key):
        """Record quiz answers as understanding assessments, then return the new route."""
        human = self.graph.human(user)
        with self.graph.transaction():
            members = human.snapshot().nodes
            for index, (concept_id, choice) in enumerate(answers):
                topic = self.curriculum.topic(concept_id)
                outcome = "unsure" if choice is None else "correct" if choice == topic.answer else "wrong"
                options = dict(method="diagnostic_quiz", expected_revision=expected_revision if index == 0 else None,
                               provenance={"question": topic.question, "outcome": outcome})
                if concept_id in members:
                    human.assess_understanding(concept_id, QUIZ_SCORES[outcome], confidence=QUIZ_CONFIDENCE,
                                               idempotency_key=f"{key}-{index}", **options)
                else:
                    human.add_concept(concept_id, understanding=QUIZ_SCORES[outcome], **options)
            self._link(human, [concept_id for concept_id, _ in answers])
        return self.route(user, target)

    def progress(self, user, concept_id, score, previous, expected_revision, key):
        """A lesson self-rating: updates or adds the concept, and the edge just learned across."""
        human = self.graph.human(user)
        self.curriculum.topic(concept_id)
        with self.graph.transaction():
            snapshot = human.snapshot()
            if concept_id in snapshot.nodes:
                human.assess_understanding(concept_id, score, method="lesson_self_report", idempotency_key=key,
                                           expected_revision=expected_revision)
            else:
                human.add_concept(concept_id, understanding=score, method="lesson_self_report",
                                  expected_revision=expected_revision)
            self._link(human, [concept_id])
            edge = tuple(sorted((concept_id, previous))) if previous else None
            snapshot = human.snapshot()
            if edge in snapshot.edges:
                human.assess_familiarity(previous, concept_id, score, method="lesson_self_report",
                                         idempotency_key=f"{key}-edge")
        return {"revision": human.revision}

    # ---- lessons -------------------------------------------------------------------------
    def tag(self, text, lesson: LessonPlan) -> LessonPlan:
        """Attach the curriculum concept a free-text lesson taught, so its rating can count."""
        concept_id = self.curriculum.match(text)
        topic = self.curriculum.by_id.get(concept_id)
        if topic and topic.recipe and authored(topic.recipe).recipe_id == lesson.recipe_id:
            lesson.concept_id = concept_id
        return lesson

    def concept_lesson(self, concept_id) -> LessonPlan:
        topic = self.curriculum.topic(concept_id)
        lesson = self.planner(topic.recipe) if topic.recipe else None
        if lesson is None or lesson.recipe_id == "whiteboard_v1":
            lesson = concept_card(topic)
        lesson.concept_id = concept_id
        return lesson

    def route_lesson(self, user, target) -> LessonPlan:
        route = self.route(user, target)
        if route["status"] == "needs_diagnostic":
            raise GoalError("Take the starting check for this goal first")
        snapshot = self.graph.human(user).snapshot()
        lesson = route_map(route, snapshot, set(self.region(target)), self.curriculum)
        lesson.concept_id = target
        return lesson


def _wrap(text, width=46):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


def concept_card(topic: Topic) -> LessonPlan:
    """Authored text on a whiteboard; used where no verified visual recipe exists."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", topic.description) if s]
    nodes = [Node(id="title", content=Label(text=topic.name, position=[0.0, 3.6]))]
    beats, y = [], 2.4
    for i, sentence in enumerate(sentences, start=1):
        ids = []
        for j, line in enumerate(_wrap(sentence)):
            nodes.append(Node(id=f"line_{i}_{j}", content=Label(text=line, position=[0.0, round(y, 2)])))
            ids.append(f"line_{i}_{j}")
            y -= 0.9
        y -= 0.3
        beats.append(TeachingBeat(beat_id=f"idea_{i}", say_text=sentence, focus_ids=ids))
    return LessonPlan(recipe_id="concept_card_v1", nodes=nodes[:MAX_OBJECTS], beats=beats)


def route_map(route, snapshot, region, curriculum) -> LessonPlan:
    """The learner's map: route concepts left to right, nearby concepts above and below."""
    path = [step["id"] for step in route["steps"]]
    others = [c for c in region if c in snapshot.nodes and c not in path]
    # Keep inside the scene budget: 2 objects per concept, 1 per edge, plus the summary.
    while others and 2 * (len(path) + len(others)) + 1 + sum(
            1 for a, b in snapshot.edges if a in set(path) | set(others) and b in set(path) | set(others)
            ) > MAX_OBJECTS:
        others.pop()
    shown = path + others
    positions = {c: (round(-4.0 + 8.0 * i / max(len(path) - 1, 1), 3) if len(path) > 1 else 0.0, 0.0)
                 for i, c in enumerate(path)}
    # Off-route concepts linked to each other share a row, so their edge stays horizontal.
    groups, assigned = [], set()
    for concept_id in others:
        if concept_id in assigned:
            continue
        group, stack = [], [concept_id]
        while stack:
            current = stack.pop()
            if current in assigned:
                continue
            assigned.add(current)
            group.append(current)
            stack += [n for n in snapshot.neighbors(current) if n in others and n not in assigned]
        groups.append(group)
    rows = {ROW: [], -ROW: []}
    for group in groups:
        y = ROW if len(rows[ROW]) <= len(rows[-ROW]) else -ROW
        for concept_id in group:
            anchors = [positions[n][0] for n in snapshot.neighbors(concept_id) if n in positions]
            rows[y].append((sum(anchors) / len(anchors) if anchors else 0.0, concept_id))
    for y, row in rows.items():
        last = None
        for x, concept_id in sorted(row):
            x = max(x, last + 3.2) if last is not None else x
            positions[concept_id] = (round(min(x, 4.5), 3), y)
            last = x

    def label(concept_id):
        node = snapshot.nodes[concept_id]
        short = curriculum.by_id[concept_id].short if concept_id in curriculum.by_id else node.label
        return f"{short} · {node.understanding:g}"

    def width(text):  # Fitted label plate width; see renderer text_label.
        return 0.24 * len(text) + 0.26

    # Route labels go on the side no off-route edge arrives from, without overlapping a neighbour.
    sides, last_on = {}, {1: None, -1: None}
    for i, concept_id in enumerate(path):
        x, size = positions[concept_id][0], width(label(concept_id))
        arriving = {1 if positions[n][1] > 0 else -1 for n in snapshot.neighbors(concept_id)
                    if n in positions and positions[n][1] != 0}

        def penalty(side):
            # Overlapping another label is worse than a plate covering part of an edge.
            prev = last_on[side]
            overlaps = prev is not None and abs(x - prev[0]) < (size + prev[1]) / 2
            return 2 * overlaps + (side in arriving)
        preferred = 1 if i % 2 == 0 else -1
        side = min((preferred, -preferred), key=penalty)
        sides[concept_id] = side
        last_on[side] = (x, size)

    nodes = []
    for i, concept_id in enumerate(shown):
        x, y = positions[concept_id]
        known = snapshot.nodes[concept_id].understanding >= KNOWN
        circle = [(x + 0.45 * c, y + 0.45 * s) for c, s in
                  ((1, 0), (0.92, 0.38), (0.71, 0.71), (0.38, 0.92), (0, 1), (-0.38, 0.92), (-0.71, 0.71),
                   (-0.92, 0.38), (-1, 0), (-0.92, -0.38), (-0.71, -0.71), (-0.38, -0.92), (0, -1),
                   (0.38, -0.92), (0.71, -0.71), (0.92, -0.38), (1, 0))]
        nodes.append(Node(id=f"c{i}", style="primary" if known else "muted",
                          content=Curve(points=[[round(px, 4), round(py, 4)] for px, py in circle])))
        offset = 0.95 * sides[concept_id] if y == 0 else (0.9 if y > 0 else -0.9)
        nodes.append(Node(id=f"l{i}", content=Label(text=label(concept_id), position=[x, y + offset])))
    index = {c: i for i, c in enumerate(shown)}
    on_route = {frozenset(pair) for pair in zip(path, path[1:])}
    edge_ids = {}
    for (a, b), edge in sorted(snapshot.edges.items()):
        if a in index and b in index:
            (ax, ay), (bx, by) = positions[a], positions[b]
            length = max(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5, 1e-6)
            dx, dy = (bx - ax) / length * 0.45, (by - ay) / length * 0.45
            edge_id = f"e{index[a]}_{index[b]}"
            edge_ids[frozenset((a, b))] = edge_id
            nodes.append(Node(id=edge_id, style="secondary" if frozenset((a, b)) in on_route else "muted",
                              content=Curve(points=[[round(ax + dx, 4), round(ay + dy, 4)],
                                                    [round(bx - dx, 4), round(by - dy, 4)]])))
    target_name = route["target"]["name"]
    names = [step["name"] for step in route["steps"]]
    known_steps = [s for s in route["steps"] if s["known"]]
    nodes.append(Node(id="summary", content=Label(text=f"Route to {target_name}", position=[0.0, 4.4])))
    path_focus = [f"c{index[c]}" for c in path] + [f"l{index[c]}" for c in path] + [
        edge_ids[k] for k in on_route if k in edge_ids]
    if route["status"] == "known":
        beats = [TeachingBeat(beat_id="known", say_text=f"You already know {target_name} well. "
                              "Pick another goal, or review it to strengthen it.", focus_ids=path_focus)]
    else:
        first = (f"You already know {', '.join(s['name'] for s in known_steps)} well, so the route starts there."
                 if known_steps else f"Nothing nearby is familiar yet, so we start from {names[0]}.")
        nxt = next((s["name"] for s in route["steps"] if not s["known"]), target_name)
        beats = [
            TeachingBeat(beat_id="start", say_text=first, focus_ids=path_focus[:1] + path_focus[len(path):len(path) + 1]),
            TeachingBeat(beat_id="route", say_text=f"The cheapest route to {target_name} is "
                         f"{' → '.join(names)}.", focus_ids=path_focus),
            TeachingBeat(beat_id="why", say_text="A step costs less when the two ideas are closely related and "
                         "you understand them well, so the route leans on what you know.",
                         focus_ids=[edge_ids[k] for k in on_route if k in edge_ids] or path_focus),
            TeachingBeat(beat_id="next", say_text=f"Next up: {nxt}. Select Learn next to begin.",
                         focus_ids=[f"c{index[c]}" for c in path if c == route["next"]] or path_focus),
        ]
    return LessonPlan(recipe_id="learning_route_v1", nodes=nodes, beats=beats)

