"""Indexed dispatch fences and durable command reconciliation evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_runtime_recovery"
down_revision = "0010_hmac_key_version"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "runtime_commands",
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("runtime_commands", sa.Column("dispatch_sequence", sa.BigInteger()))
    op.add_column("runtime_commands", sa.Column("received_at", sa.DateTime(timezone=True)))
    op.add_column("runtime_commands", sa.Column("barrier_at", sa.DateTime(timezone=True)))
    op.add_column("runtime_commands", sa.Column("resolution", JSONB()))
    op.add_column(
        "runtime_observations", sa.Column("source_observed_at", sa.DateTime(timezone=True))
    )
    op.execute(
        "UPDATE runtime_commands SET blocked = true WHERE stage = 'timed_out' OR "
        "(stage = 'failed' AND EXISTS (SELECT 1 FROM runtime_receipts r WHERE "
        "r.command_id = runtime_commands.id AND r.error_code = 'execution_uncertain'))"
    )
    op.create_index(
        "ix_command_dispatch", "runtime_commands", ["edge_id", "stage", "created_at", "id"]
    )
    op.create_index("ix_command_blocked", "runtime_commands", ["edge_id", "blocked"])
    op.create_index("ix_observation_received", "runtime_observations", ["received_at"])
    op.create_index(
        "ix_command_reconciliation_observation",
        "runtime_commands",
        [sa.text("(resolution ->> 'observation_id')")],
    )
    op.create_index("ix_state_observation", "runtime_state", ["observation_id"])


def downgrade():
    raise RuntimeError("This migration is forward-only; restore a verified backup instead")
