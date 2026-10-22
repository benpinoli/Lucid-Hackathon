"""Storage-independent values and errors. Public records are immutable."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from numbers import Real
from typing import Literal, Protocol
from uuid import UUID, uuid4


class GraphError(Exception):
    """Base class for expected domain failures."""


class ValidationError(GraphError, ValueError):
    pass


class NotFound(GraphError, LookupError):
    pass


class AmbiguousConcept(GraphError):
    pass


class Conflict(GraphError):
    pass


class DependencyError(Conflict):
    pass


class MissingGodEdge(DependencyError):
    pass


class ResourceLimit(GraphError):
    pass


def uid(value: str | None = None) -> str:
    try:
        return str(UUID(value)) if value is not None else str(uuid4())
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Expected a UUID") from exc


def pair(a: str, b: str) -> tuple[str, str]:
    a, b = uid(a), uid(b)
    if a == b:
        raise ValidationError("Self-loops are not supported")
    return tuple(sorted((a, b)))


def finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValidationError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValidationError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be a finite number")
    return result


def score(value: float) -> float:
    value = finite(value, "score")
    if not 1 <= value <= 10:
        raise ValidationError("Scores must be between 1 and 10")
    return value


def text(value: str, name: str = "name") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} cannot be empty")
    return value.strip()


def timestamp(value: str | datetime | None = None) -> str:
    try:
        parsed = (
            datetime.now(timezone.utc)
            if value is None
            else (datetime.fromisoformat(value) if isinstance(value, str) else value)
        )
        if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Timestamp must include a timezone") from exc


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Value must be finite JSON data") from exc


@dataclass(frozen=True)
class Methodology:
    formula_id: str = "baseline_v1"
    version: int = 1
    epsilon: float = 1e-9
    tie_breaking: str = "sorted_ids_first_discovery_strict_improvement"
    score_min: float = 1.0
    score_max: float = 10.0
    expression: str = "1 / (similarity * (understanding_a + understanding_b) + epsilon)"
    changes: tuple[str, ...] = ()

    def __post_init__(self):
        if (
            self.formula_id != "baseline_v1"
            or type(self.version) is not int
            or self.version != 1
            or self.tie_breaking != "sorted_ids_first_discovery_strict_improvement"
            or finite(self.score_min, "score_min") != 1
            or finite(self.score_max, "score_max") != 10
            or self.expression != "1 / (similarity * (understanding_a + understanding_b) + epsilon)"
            or self.changes not in ((), [])
        ):
            raise ValidationError("Unsupported methodology; register a new policy explicitly")
        epsilon = finite(self.epsilon, "epsilon")
        if epsilon <= 0:
            raise ValidationError("epsilon must be positive")
        object.__setattr__(self, "epsilon", epsilon)
        object.__setattr__(self, "score_min", 1.0)
        object.__setattr__(self, "score_max", 10.0)
        object.__setattr__(self, "changes", ())

    @property
    def configuration_hash(self) -> str:
        # Model ID/version fixes expression, bounds and changelog. Hash the variable
        # operative contract, so adding explanatory metadata cannot change identity.
        operative = {
            key: getattr(self, key) for key in ("formula_id", "version", "epsilon", "tie_breaking")
        }
        return hashlib.sha256(canonical_json(operative).encode()).hexdigest()

    def cost(
        self,
        similarity: float,
        understanding_a: float,
        understanding_b: float,
        familiarity: float | None = None,
    ) -> float:
        result = 1.0 / (
            score(similarity) * (score(understanding_a) + score(understanding_b)) + self.epsilon
        )
        if not math.isfinite(result) or result <= 0:
            raise ValidationError("Traversal cost must be finite and positive")
        return result


class CostPolicy(Protocol):
    formula_id: str
    version: int
    epsilon: float
    configuration_hash: str

    def cost(
        self,
        similarity: float,
        understanding_a: float,
        understanding_b: float,
        familiarity: float | None = None,
    ) -> float: ...


@dataclass(frozen=True)
class Concept:
    id: str
    canonical_name: str
    description: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Revision:
    database_id: str
    god: int
    human: int | None = None
    human_id: str | None = None


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    description: str
    understanding: float | None = None
    assessment_id: str | None = None
    in_human: bool = False


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    similarity: float
    familiarity: float | None = None
    similarity_assessment_id: str | None = None
    familiarity_assessment_id: str | None = None
    in_human: bool = False


@dataclass(frozen=True)
class PathStep:
    source: str
    target: str
    similarity: float
    understanding_source: float
    understanding_target: float
    familiarity: float
    cost: float
    similarity_assessment_id: str
    understanding_source_assessment_id: str
    understanding_target_assessment_id: str
    familiarity_assessment_id: str


@dataclass(frozen=True)
class PathResult:
    status: Literal["reachable", "unreachable"]
    source: str | None
    target: str
    concepts: tuple[str, ...]
    steps: tuple[PathStep, ...]
    total_cost: float | None
    revision: Revision
    methodology: Methodology

    def to_dict(self) -> dict:
        return {**asdict(self), "configuration_hash": self.methodology.configuration_hash}
