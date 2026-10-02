"""Transaction-ordered bounded captured device events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_events"
down_revision = "0007_services"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "device_event_cursors",
        sa.Column("domain_id", sa.String(32), sa.ForeignKey("access_domains.id"), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "device_events",
        sa.Column("domain_id", sa.String(32), sa.ForeignKey("access_domains.id"), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column("thing_id", sa.String(32), sa.ForeignKey("registry_devices.id"), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("device_events")
    op.drop_table("device_event_cursors")
