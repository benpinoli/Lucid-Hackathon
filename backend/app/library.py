"""Durable, user-scoped lesson artifacts; graph definitions and scores stay in the engine."""
import json

from sqlalchemy import Column, MetaData, String, Table, Text, insert, select, update

from .teaching import Check
from .learning import LessonPlan


lessons = Table("lucid_lessons_v1", MetaData(),
                Column("human_id", String, primary_key=True),
                Column("concept_id", String, primary_key=True),
                Column("plan", Text, nullable=False), Column("check_json", Text, nullable=False))


class LessonLibrary:
    def __init__(self, graph):
        self.graph = graph
        with graph.store.transaction(write=True) as connection:
            lessons.create(connection, checkfirst=True)

    def save(self, user, concept, plan, check):
        # Save the explanation before tutor prompts/reminders are composed into it.
        artifact = plan.model_dump()
        artifact["nodes"] = [node.model_dump() for node in plan.nodes]
        values = {"plan": json.dumps(artifact),
                  "check_json": json.dumps(check.model_dump() if check else None)}
        with self.graph.store.transaction(write=True) as connection:
            query = lessons.c.human_id == user
            query &= lessons.c.concept_id == concept
            if connection.execute(select(lessons.c.concept_id).where(query)).first():
                connection.execute(update(lessons).where(query).values(**values))
            else:
                connection.execute(insert(lessons).values(human_id=user, concept_id=concept, **values))

    def get(self, user, concept):
        with self.graph.store.transaction() as connection:
            row = connection.execute(select(lessons).where(
                lessons.c.human_id == user, lessons.c.concept_id == concept)).mappings().first()
        if row is None:
            return None
        check = json.loads(row["check_json"])
        return LessonPlan.model_validate_json(row["plan"]), Check.model_validate(check) if check else None

    def listing(self, user):
        with self.graph.store.transaction() as connection:
            return [{"concept_id": row.concept_id} for row in connection.execute(
                select(lessons.c.concept_id).where(lessons.c.human_id == user)
                .order_by(lessons.c.concept_id))]
