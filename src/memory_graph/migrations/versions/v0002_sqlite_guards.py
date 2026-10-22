"""Protect active dependency and audit invariants from accidental direct SQL writes."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Implement equivalent guards before enabling another SQL adapter")

    def trigger(name, table, action, condition, message):
        connection.exec_driver_sql(
            f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN {condition} "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    dependencies = {
        "god_edges": "NOT EXISTS (SELECT 1 FROM concepts WHERE concept_id=NEW.a_id AND active=1) "
        "OR NOT EXISTS (SELECT 1 FROM concepts WHERE concept_id=NEW.b_id AND active=1)",
        "human_concepts": "NOT EXISTS (SELECT 1 FROM humans WHERE human_id=NEW.human_id AND active=1) "
        "OR NOT EXISTS (SELECT 1 FROM concepts WHERE concept_id=NEW.concept_id AND active=1)",
        "human_edges": "NOT EXISTS (SELECT 1 FROM human_concepts WHERE human_id=NEW.human_id "
        "AND concept_id=NEW.a_id AND active=1) OR NOT EXISTS (SELECT 1 FROM "
        "human_concepts WHERE human_id=NEW.human_id AND concept_id=NEW.b_id AND active=1) "
        "OR NOT EXISTS (SELECT 1 FROM god_edges WHERE a_id=NEW.a_id AND b_id=NEW.b_id AND active=1)",
    }
    for table, condition in dependencies.items():
        for action in ("INSERT", "UPDATE"):
            trigger(
                f"guard_{table}_{action.lower()}",
                table,
                action,
                f"NEW.active=1 AND ({condition})",
                "Inactive graph dependency",
            )
    dependents = {
        "concepts": "EXISTS (SELECT 1 FROM god_edges WHERE active=1 AND "
        "(a_id=NEW.concept_id OR b_id=NEW.concept_id)) OR EXISTS "
        "(SELECT 1 FROM human_concepts WHERE active=1 AND concept_id=NEW.concept_id)",
        "god_edges": "EXISTS (SELECT 1 FROM human_edges WHERE active=1 AND a_id=NEW.a_id AND b_id=NEW.b_id)",
        "human_concepts": "EXISTS (SELECT 1 FROM human_edges WHERE active=1 AND human_id=NEW.human_id "
        "AND (a_id=NEW.concept_id OR b_id=NEW.concept_id))",
        "humans": "EXISTS (SELECT 1 FROM human_concepts WHERE active=1 AND human_id=NEW.human_id)",
    }
    for table, condition in dependents.items():
        trigger(
            f"guard_{table}_deactivate",
            table,
            "UPDATE",
            f"NEW.active=0 AND ({condition})",
            "Active dependent graph record",
        )
    for kind, table in (
        ("similarity", "god_edges"),
        ("understanding", "human_concepts"),
        ("familiarity", "human_edges"),
    ):
        keys = {
            "similarity": ("a_id", "b_id"),
            "understanding": ("human_id", "concept_id"),
            "familiarity": ("human_id", "a_id", "b_id"),
        }[kind]
        same_subject = " AND ".join(f"newer.{key}=a.{key}" for key in keys)
        trigger(
            f"guard_{table}_score",
            table,
            "UPDATE OF score, current_assessment_id",
            "NEW.current_assessment_id IS NULL OR NOT EXISTS (SELECT 1 FROM "
            f"{kind}_assessments a JOIN assessment_decisions d ON d.{kind}_id=a.assessment_id "
            "WHERE a.assessment_id=NEW.current_assessment_id AND a.score=NEW.score AND d.accepted=1 "
            f"AND NOT EXISTS (SELECT 1 FROM {kind}_assessments newer JOIN assessment_decisions nd "
            f"ON nd.{kind}_id=newer.assessment_id WHERE {same_subject} "
            "AND nd.accepted=1 AND nd.sequence>d.sequence))",
            "Current score must reference an accepted assessment",
        )
    for table in (
        "similarity_assessments",
        "understanding_assessments",
        "familiarity_assessments",
        "assessment_decisions",
        "mutation_events",
        "methodology_versions",
        "archive_origins",
    ):
        for action in ("UPDATE", "DELETE"):
            trigger(
                f"immutable_{table}_{action.lower()}", table, action, "1", "Immutable audit record"
            )


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup")
