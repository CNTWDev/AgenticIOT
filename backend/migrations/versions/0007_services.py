"""Node-local services and metadata-only inference records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_services"
down_revision = "0006_nodes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "service_connections",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("domain_id", sa.String(32), sa.ForeignKey("access_domains.id"), nullable=False),
        sa.Column("node_id", sa.String(32), sa.ForeignKey("runtime_edges.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("local_ref", sa.String(64), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("declared_by", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("domain_id", "name", name="uq_service_name"),
    )
    op.create_index("ix_service_connections_domain_id", "service_connections", ["domain_id"])
    op.create_table(
        "service_invocations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("domain_id", sa.String(32), sa.ForeignKey("access_domains.id"), nullable=False),
        sa.Column(
            "service_id", sa.String(32), sa.ForeignKey("service_connections.id"), nullable=False
        ),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("key_hash", sa.String(64)),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("owner_instance_id", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("usage", JSONB()),
        sa.UniqueConstraint("scope", "key_hash", name="uq_invocation_key"),
    )
    op.create_index("ix_service_invocations_domain_id", "service_invocations", ["domain_id"])


def downgrade():
    op.drop_table("service_invocations")
    op.drop_table("service_connections")
