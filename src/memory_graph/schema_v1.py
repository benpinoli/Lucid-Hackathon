"""Frozen initial schema. Future migrations must not modify this module."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def lifecycle():
    return [
        Column("active", Boolean, nullable=False, default=True),
        Column("created_at", String, nullable=False),
        Column("updated_at", String, nullable=False),
    ]


state = Table(
    "graph_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("database_id", String, nullable=False),
    Column(
        "configuration_hash",
        String,
        ForeignKey("methodology_versions.configuration_hash"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    CheckConstraint("id = 1", name="singleton"),
)
humans = Table(
    "humans",
    metadata,
    Column("human_id", String, primary_key=True),
    Column("revision", Integer, nullable=False),
    *lifecycle(),
)
concepts = Table(
    "concepts",
    metadata,
    Column("concept_id", String, primary_key=True),
    Column("canonical_name", String, nullable=False),
    Column("normalized_name", String, nullable=False, index=True),
    Column("description", String, nullable=False),
    *lifecycle(),
)
aliases = Table(
    "concept_aliases",
    metadata,
    Column("concept_id", String, ForeignKey("concepts.concept_id"), primary_key=True),
    Column("alias", String, primary_key=True),
    Column("normalized_alias", String, nullable=False, index=True),
)
god_edges = Table(
    "god_edges",
    metadata,
    Column("a_id", String, ForeignKey("concepts.concept_id"), primary_key=True),
    Column("b_id", String, ForeignKey("concepts.concept_id"), primary_key=True),
    Column("score", Float, nullable=False),
    Column("current_assessment_id", String),
    *lifecycle(),
    CheckConstraint("a_id < b_id", name="ordered"),
    CheckConstraint("score >= 1 AND score <= 10", name="score"),
)
Index("ix_god_edges_b", god_edges.c.b_id)
human_concepts = Table(
    "human_concepts",
    metadata,
    Column("human_id", String, ForeignKey("humans.human_id"), primary_key=True),
    Column("concept_id", String, ForeignKey("concepts.concept_id"), primary_key=True),
    Column("score", Float, nullable=False),
    Column("current_assessment_id", String),
    *lifecycle(),
    CheckConstraint("score >= 1 AND score <= 10", name="score"),
)
human_edges = Table(
    "human_edges",
    metadata,
    Column("human_id", String, ForeignKey("humans.human_id"), primary_key=True),
    Column("a_id", String, primary_key=True),
    Column("b_id", String, primary_key=True),
    Column("score", Float, nullable=False),
    Column("current_assessment_id", String),
    *lifecycle(),
    CheckConstraint("a_id < b_id", name="ordered"),
    CheckConstraint("score >= 1 AND score <= 10", name="score"),
    ForeignKeyConstraint(["a_id", "b_id"], ["god_edges.a_id", "god_edges.b_id"]),
    ForeignKeyConstraint(
        ["human_id", "a_id"], ["human_concepts.human_id", "human_concepts.concept_id"]
    ),
    ForeignKeyConstraint(
        ["human_id", "b_id"], ["human_concepts.human_id", "human_concepts.concept_id"]
    ),
)
Index("ix_human_edges_b", human_edges.c.human_id, human_edges.c.b_id)

ASSESSMENTS = {}
SUBJECTS = {
    "understanding": (human_concepts, ("human_id", "concept_id")),
    "familiarity": (human_edges, ("human_id", "a_id", "b_id")),
    "similarity": (god_edges, ("a_id", "b_id")),
}
for kind, (subject, keys) in SUBJECTS.items():
    table = Table(
        f"{kind}_assessments",
        metadata,
        Column("assessment_id", String, primary_key=True),
        *(Column(key, String, nullable=False) for key in keys),
        Column("score", Float, nullable=False),
        Column("assessed_at", String, nullable=False),
        Column("recorded_at", String, nullable=False),
        Column("method", String, nullable=False),
        Column("provenance", String, nullable=False),
        Column("confidence", Float),
        CheckConstraint("score >= 1 AND score <= 10", name="score"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence"
        ),
        ForeignKeyConstraint(list(keys), [f"{subject.name}.{key}" for key in keys]),
        UniqueConstraint(*keys, "assessment_id"),
    )
    subject.append_constraint(
        ForeignKeyConstraint(
            [*keys, "current_assessment_id"],
            [f"{table.name}.{key}" for key in (*keys, "assessment_id")],
            deferrable=True,
            initially="DEFERRED",
            name=f"fk_{subject.name}_current",
        )
    )
    Index(f"ix_{kind}_subject", *(table.c[key] for key in keys), table.c.recorded_at)
    ASSESSMENTS[kind] = table

decisions = Table(
    "assessment_decisions",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("decision_id", String, nullable=False, unique=True),
    *(
        Column(f"{kind}_id", String, ForeignKey(f"{kind}_assessments.assessment_id"), unique=True)
        for kind in ASSESSMENTS
    ),
    Column("accepted", Boolean, nullable=False),
    Column("decided_at", String, nullable=False),
    CheckConstraint(
        "(CASE WHEN understanding_id IS NULL THEN 0 ELSE 1 END + "
        "CASE WHEN familiarity_id IS NULL THEN 0 ELSE 1 END + "
        "CASE WHEN similarity_id IS NULL THEN 0 ELSE 1 END) = 1",
        name="one_subject",
    ),
)
events = Table(
    "mutation_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String, nullable=False, unique=True),
    Column("scope", String, nullable=False, index=True),
    Column("revision", Integer, nullable=False),
    Column("operation", String, nullable=False),
    Column("payload", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("scope", "revision"),
)
methodologies = Table(
    "methodology_versions",
    metadata,
    Column("configuration_hash", String, primary_key=True),
    Column("configuration", String, nullable=False),
)
retries = Table(
    "idempotency_keys",
    metadata,
    Column("scope", String, primary_key=True),
    Column("key", String, primary_key=True),
    Column("request", String, nullable=False),
    Column("result", String, nullable=False),
)
proposals = Table(
    "proposals",
    metadata,
    Column("proposal_id", String, primary_key=True),
    Column("payload", String, nullable=False),
    Column("status", String, nullable=False),
    Column("resolution", String),
    Column("created_at", String, nullable=False),
    Column("resolved_at", String),
    CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="status"),
)
origins = Table(
    "archive_origins",
    metadata,
    Column("import_id", String, primary_key=True),
    Column("source_database_id", String, nullable=False),
    Column("source_god_revision", Integer, nullable=False),
    Column("archive_hash", String, nullable=False),
    Column("imported_at", String, nullable=False),
)
