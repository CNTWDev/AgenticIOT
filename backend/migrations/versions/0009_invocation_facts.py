"""Late inference facts without resurrecting terminal states or retaining text."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_invocation_facts"
down_revision = "0008_events"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("service_invocations", sa.Column("late_report", JSONB()))
    op.create_index("ix_invocation_deadline", "service_invocations", ["deadline"])


def downgrade():
    op.drop_index("ix_invocation_deadline", table_name="service_invocations")
    op.drop_column("service_invocations", "late_report")
