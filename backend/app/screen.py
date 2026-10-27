"""What the learner is looking at, in words, for the author model.

The scene is the server's own record of what is displayed; the client adds what only it
knows (slider positions it moved, the step it is on, a running animation, VR). Formula-driven
geometry is evaluated at the learner's current slider values, so the model reads where things
actually are, not only their formulas.
"""
from __future__ import annotations

import math

from .expressions import ExpressionError, compile_expression

MAX_LINES = 40


def _value(source, scope):
    try:
        function, _ = compile_expression(source)
    except ExpressionError:
        return None
    result = function(scope)
    return round(result, 2) if math.isfinite(result) else None


def _point(sources, scope):
    values = [_value(s, scope) for s in sources]
    if any(v is None for v in values):
        return "off-screen or undefined at these slider values"
    return "(" + ", ".join(f"{v:g}" for v in values) + ")"


def _numbers(point):
    return "(" + ", ".join(f"{round(v, 2):g}" for v in point) + ")"


def describe_nodes(nodes, sliders=None) -> tuple[list[str], dict[str, float]]:
    """One line per visible element, plus the parameter values used to place them."""
    sliders = sliders or {}
    scope = {}
    for node in nodes:
        c = node.content
        if c.primitive == "slider" and c.binding.startswith("param."):
            scope[c.binding[6:]] = float(sliders.get(c.binding, c.value))
    lines = []
    for node in nodes:
        if not node.visible:
            continue
        c, style = node.content, f" [{node.style}]"
        if c.primitive == "axes2d":
            lines.append(f"2D axes from -{c.extent:g} to {c.extent:g}")
        elif c.primitive == "axes3d":
            lines.append(f"3D axes (x={c.labels[0]}, y={c.labels[1]}, z={c.labels[2]}) to ±{c.extent:g}")
        elif c.primitive == "vector_arrow":
            lines.append(f"arrow {node.id} from {_numbers(c.start)} to {_numbers(c.end)}{style}")
        elif c.primitive == "line_curve":
            lines.append(f"polyline {node.id} with {len(c.points)} points from {_numbers(c.points[0])} "
                         f"to {_numbers(c.points[-1])}{style}")
        elif c.primitive == "text_label":
            lines.append(f"label {node.id}: \"{c.text}\" at {_numbers(c.position)}")
        elif c.primitive == "expr_curve":
            z = f", z={c.z}" if c.z else ""
            lines.append(f"curve {node.id}: x={c.x}, y={c.y}{z} for t in [{c.t_min:g}, {c.t_max:g}]{style}")
        elif c.primitive == "expr_arrow":
            lines.append(f"arrow {node.id} from {_point(c.start, scope)} to {_point(c.end, scope)} "
                         f"(formulas {list(c.start)} -> {list(c.end)}){style}")
        elif c.primitive == "expr_point":
            text = f" labelled \"{c.text}\"" if c.text else ""
            lines.append(f"point {node.id}{text} now at {_point(c.at, scope)} (formula {list(c.at)})")
        elif c.primitive == "expr_surface":
            lines.append(f"3D surface {node.id}: x={c.x}, y={c.y}, z={c.z} over u in [{c.u_min:g}, {c.u_max:g}], "
                         f"v in [{c.v_min:g}, {c.v_max:g}]{style}")
        elif c.primitive == "model3d":
            clip = f", playing '{c.clip}'" if c.clip else ""
            lines.append(f"3D model {node.id} ({c.model}) at {_point(c.position, scope)}{clip}")
        elif c.primitive == "slider":
            name = c.label or c.binding
            moved = c.binding in sliders and sliders[c.binding] != c.value
            now = f"learner moved it to {sliders[c.binding]:g}" if moved else f"at its default {c.value:g}"
            lines.append(f"slider '{name}' ({c.binding}) range [{c.minimum:g}, {c.maximum:g}], {now}")
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"... and {len(lines) - MAX_LINES} more elements"]
    return lines, scope


def describe_screen(nodes, beats, view=None, question=None, exchanges=()) -> dict:
    """The learner's screen and recent conversation, as the author model's context."""
    view = view or {}
    sliders = view.get("sliders") or {}
    diagram, scope = describe_nodes(nodes, sliders)
    active = view.get("active_beat")
    steps = [{"step": i + 1, "text": text, **({"active": True} if i == active else {})}
             for i, text in enumerate(beats)]
    moved = {b: v for b, v in sliders.items()
             if any(n.content.primitive == "slider" and n.content.binding == b and n.content.value != v for n in nodes)}
    return {
        "diagram": diagram or ["(nothing drawn yet)"],
        "spoken_steps": steps,
        "learner_view": {
            "active_step": active + 1 if isinstance(active, int) else None,
            "sliders_moved_by_learner": moved,
            "current_parameter_values": scope,
            "animating": view.get("animating"),
            "in_vr": bool(view.get("in_vr")),
        },
        "pending_question": question,
        "recent_exchanges": list(exchanges),
    }
