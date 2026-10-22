"""LLM integration boundary: untrusted candidates never mutate graphs directly."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Protocol

from sqlalchemy import insert, select, update

from . import schema_v1 as db
from .domain import (
    Conflict,
    NotFound,
    ValidationError,
    canonical_json,
    finite,
    score,
    text,
    timestamp,
    uid,
)

# Explicit allowlist: no dynamically selected methods or arbitrary keyword arguments.
COMMANDS = {
    "add_concept": ({"concept_id", "name"}, {"description"}),
    "connect_god": ({"a", "b", "similarity", "provenance"}, {"method"}),
    "add_human_concept": ({"human_id", "concept", "understanding"}, {"method", "provenance"}),
    "connect_human": ({"human_id", "a", "b", "familiarity"}, {"method", "provenance"}),
    "assess_understanding": ({"human_id", "concept", "understanding"}, {"method", "provenance"}),
    "assess_familiarity": ({"human_id", "a", "b", "familiarity"}, {"method", "provenance"}),
    "assess_similarity": ({"a", "b", "similarity", "provenance"}, {"method"}),
}


class ProposalProvider(Protocol):
    def propose(self, context: dict) -> dict:
        """Return candidate data matching validate_proposal; no storage access."""
        ...


def validate_proposal(payload: dict) -> dict:
    try:
        payload = json.loads(canonical_json(payload))
        if set(payload) != {"source", "evidence", "confidence", "expected_revisions", "commands"}:
            raise ValidationError("Unexpected proposal fields")
        text(payload["source"], "proposal source")
        if not isinstance(payload["evidence"], list):
            raise ValidationError("Evidence must be a list")
        if not 0 <= finite(payload["confidence"], "confidence") <= 1:
            raise ValidationError("Proposal confidence must be in [0, 1]")
        revisions = payload["expected_revisions"]
        if not isinstance(revisions, dict) or "god" not in revisions:
            raise ValidationError("Proposals must declare the God Brain revision")
        for scope, revision in revisions.items():
            if scope != "god":
                uid(scope)
            if type(revision) is not int or revision < 0:
                raise ValidationError("Invalid expected revision")
        commands = payload["commands"]
        if not isinstance(commands, list) or not 1 <= len(commands) <= 1000:
            raise ValidationError("Proposal must contain 1..1000 commands")
        for command in commands:
            if set(command) != {"operation", "arguments"} or command["operation"] not in COMMANDS:
                raise ValidationError("Unsupported proposal command")
            arguments = command["arguments"]
            required, optional = COMMANDS[command["operation"]]
            if not required <= set(arguments) or not set(arguments) <= required | optional:
                raise ValidationError("Invalid proposal command arguments")
            for name, value in arguments.items():
                if name in ("human_id", "concept_id", "a", "b", "concept"):
                    if uid(value) != value:
                        raise ValidationError("Proposal references must be canonical IDs")
                if name in ("similarity", "understanding", "familiarity"):
                    score(value)
            if "human_id" in arguments and arguments["human_id"] not in revisions:
                raise ValidationError("Proposal must declare every affected Human Brain revision")
        return payload
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValidationError("Malformed proposal") from exc


class ProposalService:
    def __init__(self, graph):
        self.graph = graph

    def submit(self, payload: dict, *, proposal_id=None) -> str:
        payload = validate_proposal(payload)
        proposal_id = uid(proposal_id)
        with self.graph.store.transaction(write=True) as connection:
            prior = (
                connection.execute(
                    select(db.proposals).where(db.proposals.c.proposal_id == proposal_id)
                )
                .mappings()
                .first()
            )
            encoded = canonical_json(payload)
            if prior:
                if prior["payload"] != encoded:
                    raise Conflict("Proposal ID already has different contents")
                return proposal_id
            connection.execute(
                insert(db.proposals).values(
                    proposal_id=proposal_id,
                    payload=encoded,
                    status="pending",
                    created_at=timestamp(),
                )
            )
        return proposal_id

    def get(self, proposal_id) -> dict:
        with self.graph.store.transaction() as connection:
            row = (
                connection.execute(
                    select(db.proposals).where(db.proposals.c.proposal_id == uid(proposal_id))
                )
                .mappings()
                .first()
            )
            if not row:
                raise NotFound("Unknown proposal")
            result = dict(row)
            for key in ("payload", "resolution"):
                result[key] = json.loads(result[key]) if result[key] is not None else None
            return result

    def reject(self, proposal_id, *, reason: str):
        proposal_id = uid(proposal_id)
        reason = text(reason, "reason")
        with self.graph.store.transaction(write=True) as connection:
            row = self.get(proposal_id)
            if row["status"] != "pending":
                raise Conflict("Proposal is already resolved")
            connection.execute(
                update(db.proposals)
                .where(db.proposals.c.proposal_id == proposal_id)
                .values(
                    status="rejected",
                    resolution=canonical_json({"reason": reason}),
                    resolved_at=timestamp(),
                )
            )

    def accept(self, proposal_id) -> dict:
        proposal_id = uid(proposal_id)
        with self.graph.store.transaction(write=True) as connection:
            row = self.get(proposal_id)
            if row["status"] == "accepted":
                return row["resolution"]
            if row["status"] != "pending":
                raise Conflict("Proposal was rejected")
            payload = validate_proposal(row["payload"])
            for scope, revision in payload["expected_revisions"].items():
                self.graph._check(connection, scope, revision)
            results = []
            for command in payload["commands"]:
                arguments = dict(command["arguments"])
                operation = command["operation"]
                if "human_id" in arguments:
                    service = self.graph.human(arguments.pop("human_id"))
                    method = {
                        "add_human_concept": "add_concept",
                        "connect_human": "connect",
                        "assess_understanding": "assess_understanding",
                        "assess_familiarity": "assess_familiarity",
                    }[operation]
                else:
                    service = self.graph.god
                    method = {
                        "add_concept": "add_concept",
                        "connect_god": "connect",
                        "assess_similarity": "assess_similarity",
                    }[operation]
                result = getattr(service, method)(**arguments)
                results.append(asdict(result) if is_dataclass(result) else result)
            resolution = {"results": results, "accepted_at": timestamp()}
            connection.execute(
                update(db.proposals)
                .where(db.proposals.c.proposal_id == proposal_id)
                .values(
                    status="accepted",
                    resolution=canonical_json(resolution),
                    resolved_at=timestamp(),
                )
            )
            return resolution
