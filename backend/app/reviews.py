"""FSRS review state is separate from assessed mastery and graph traversal weights."""
import json
from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, insert, select, update


cards = Table("lucid_review_cards_v1", MetaData(),
              Column("human_id", String, primary_key=True), Column("concept_id", String, primary_key=True),
              Column("card", Text, nullable=False), Column("mastery", Float, nullable=False),
              Column("reviews", Integer, nullable=False))
logs = Table("lucid_review_logs_v1", cards.metadata,
             Column("event_id", String, primary_key=True), Column("human_id", String, nullable=False),
             Column("concept_id", String, nullable=False), Column("evidence", Text, nullable=False))
attempts = Table("lucid_practice_attempts_v1", cards.metadata,
                 Column("event_id", String, primary_key=True), Column("human_id", String, nullable=False),
                 Column("concept_id", String, nullable=False), Column("kind", String, nullable=False),
                 Column("score", Float, nullable=False), Column("evidence", Text, nullable=False))


def utcnow():
    return datetime.now(timezone.utc)


class Reviews:
    def __init__(self, graph):
        self.graph = graph
        self.scheduler = Scheduler(desired_retention=0.9, enable_fuzzing=False)
        with graph.store.transaction(write=True) as connection:
            cards.metadata.create_all(connection)

    def log_attempt(self, user, concept, kind, score, event_id, evidence):
        """Track actual retrieval effort without treating every microcheck as a spaced review."""
        with self.graph.store.transaction(write=True) as connection:
            if not connection.execute(select(attempts.c.event_id).where(attempts.c.event_id == event_id)).first():
                connection.execute(insert(attempts).values(event_id=event_id, human_id=user, concept_id=concept,
                    kind=kind, score=score, evidence=json.dumps({**evidence, "at": utcnow().isoformat()})))

    def record(self, user, concept, mastery, event_id, evidence, now=None):
        """A scored retrieval attempt, never a page view or self-reported familiarity."""
        now = now or utcnow()
        with self.graph.store.transaction(write=True) as connection:
            if connection.execute(select(logs.c.event_id).where(logs.c.event_id == event_id)).first():
                return
            query = (cards.c.human_id == user) & (cards.c.concept_id == concept)
            row = connection.execute(select(cards).where(query)).mappings().first()
            card = Card.from_dict(json.loads(row["card"])) if row else Card(due=now)
            # Immediate post-teaching performance is weaker retention evidence than delayed recall.
            rating_score = min(mastery, 8) if evidence.get("kind") in ("teachback", "check") else mastery
            rating = Rating.Again if rating_score < 5 else Rating.Hard if rating_score < 7 else Rating.Good if rating_score < 9 else Rating.Easy
            card, review_log = self.scheduler.review_card(card, rating, review_datetime=now)
            values = {"card": json.dumps(card.to_dict()), "mastery": mastery,
                      "reviews": row["reviews"] + 1 if row else 1}
            if row:
                connection.execute(update(cards).where(query).values(**values))
            else:
                connection.execute(insert(cards).values(human_id=user, concept_id=concept, **values))
            connection.execute(insert(logs).values(event_id=event_id, human_id=user, concept_id=concept,
                evidence=json.dumps({**evidence, "mastery": mastery, "rating": rating.name,
                                     "reviewed_at": now.isoformat(), "scheduler": "fsrs-6.3.2",
                                     "review_log": review_log.to_dict()})))

    def suggestions(self, user, limit=20, now=None):
        now = now or utcnow()
        snapshot = self.graph.human(user).snapshot()
        with self.graph.store.transaction() as connection:
            saved = {r["concept_id"]: r for r in connection.execute(select(cards).where(
                cards.c.human_id == user)).mappings()}
            from sqlalchemy import func
            practice = {row.kind: row.count for row in connection.execute(select(
                attempts.c.kind, func.count().label("count")).where(attempts.c.human_id == user).group_by(attempts.c.kind))}
        result = []
        for concept, node in snapshot.nodes.items():
            row = saved.get(concept)
            card = Card.from_dict(json.loads(row["card"])) if row else None
            recall = self.scheduler.get_card_retrievability(card, current_datetime=now) if card else None
            result.append({"concept_id": concept, "name": node.label, "mastery": node.understanding,
                "last_review": card.last_review.isoformat() if card and card.last_review else None,
                "next_review": card.due.isoformat() if card else now.isoformat(),
                "recall_probability": recall, "forgetting_score": 1 - recall if recall is not None else None,
                "stability_days": card.stability if card else None,
                "difficulty": card.difficulty if card else None, "reviews": row["reviews"] if row else 0,
                "due": card.due <= now if card else True})
        # Due first, then least retrievable, then date. No inferred historical review dates.
        result.sort(key=lambda item: (not item["due"], item["recall_probability"] or 0,
                                      item["next_review"], item["concept_id"]))
        return {"scheduler": "FSRS-6", "desired_retention": 0.9, "as_of": now.isoformat(),
                "practice_attempts": sum(practice.values()), "practice_by_kind": practice,
                "due_count": sum(item["due"] for item in result), "topics": result[:limit]}
