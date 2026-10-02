"""Explicit node credentials and durable session ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0006_nodes"
down_revision = "0005_access"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "node_credentials",
        sa.Column("node_id", sa.String(32), sa.ForeignKey("runtime_edges.id"), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "node_sessions",
        sa.Column("node_id", sa.String(32), sa.ForeignKey("runtime_edges.id"), primary_key=True),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("owner_instance_id", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("node_sessions")
    op.drop_table("node_credentials")
