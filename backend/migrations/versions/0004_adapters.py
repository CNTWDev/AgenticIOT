"""Advertised Edge adapter capabilities; preserve existing virtual registrations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_adapters"
down_revision = "0003_runtime"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "runtime_edges",
        sa.Column(
            "adapters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[\"virtual-light-v1\"]'::jsonb"),
        ),
    )
    op.alter_column("runtime_edges", "adapters", server_default=None)


def downgrade():
    # Old runtime cannot safely execute MQTT bindings. Never silently reinterpret them.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM runtime_bindings WHERE adapter <> 'virtual-light-v1'")
    ):
        raise RuntimeError("MQTT bindings exist; downgrade requires an explicit data migration")
    op.drop_column("runtime_edges", "adapters")
