"""Initial durable graph schema, frozen in schema_v1."""

from alembic import op
from memory_graph.schema_v1 import metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup")
