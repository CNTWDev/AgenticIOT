"""virtual_runtime

Revision ID: 0003_runtime
Revises: 0002_registry
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_runtime"
down_revision = "0002_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reviewed additive migration; registry tables and existing records are unchanged.
    op.create_table(
        "runtime_edges",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("domain_ref", sa.String(length=200), nullable=False),
        sa.Column("edge_ref", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_ref", "edge_ref", name="uq_edge_identity"),
    )
    op.create_index(
        op.f("ix_runtime_edges_domain_ref"), "runtime_edges", ["domain_ref"], unique=False
    )
    op.create_table(
        "runtime_bindings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("domain_ref", sa.String(length=200), nullable=False),
        sa.Column("thing_id", sa.String(length=32), nullable=False),
        sa.Column("edge_id", sa.String(length=32), nullable=False),
        sa.Column("adapter", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["edge_id"],
            ["runtime_edges.id"],
        ),
        sa.ForeignKeyConstraint(
            ["thing_id"],
            ["registry_devices.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thing_id"),
    )
    op.create_index(
        op.f("ix_runtime_bindings_domain_ref"), "runtime_bindings", ["domain_ref"], unique=False
    )
    op.create_index(
        op.f("ix_runtime_bindings_edge_id"), "runtime_bindings", ["edge_id"], unique=False
    )
    op.create_table(
        "runtime_commands",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("domain_ref", sa.String(length=200), nullable=False),
        sa.Column("subject_ref", sa.String(length=200), nullable=False),
        sa.Column("thing_id", sa.String(length=32), nullable=False),
        sa.Column("binding_id", sa.String(length=32), nullable=False),
        sa.Column("edge_id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["runtime_bindings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["edge_id"],
            ["runtime_edges.id"],
        ),
        sa.ForeignKeyConstraint(
            ["thing_id"],
            ["registry_devices.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_ref", "subject_ref", "idempotency_key", name="uq_command_key"),
    )
    op.create_index(
        op.f("ix_runtime_commands_domain_ref"), "runtime_commands", ["domain_ref"], unique=False
    )
    op.create_index(
        op.f("ix_runtime_commands_edge_id"), "runtime_commands", ["edge_id"], unique=False
    )
    op.create_index(
        op.f("ix_runtime_commands_thing_id"), "runtime_commands", ["thing_id"], unique=False
    )
    op.create_table(
        "runtime_observations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("domain_ref", sa.String(length=200), nullable=False),
        sa.Column("thing_id", sa.String(length=32), nullable=False),
        sa.Column("edge_id", sa.String(length=32), nullable=False),
        sa.Column("command_id", sa.String(length=32), nullable=True),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["command_id"],
            ["runtime_commands.id"],
        ),
        sa.ForeignKeyConstraint(
            ["edge_id"],
            ["runtime_edges.id"],
        ),
        sa.ForeignKeyConstraint(
            ["thing_id"],
            ["registry_devices.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("edge_id", "source_sequence", name="uq_observation_sequence"),
    )
    op.create_index(
        op.f("ix_runtime_observations_thing_id"), "runtime_observations", ["thing_id"], unique=False
    )
    op.create_table(
        "runtime_receipts",
        sa.Column("command_id", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("executor_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["command_id"],
            ["runtime_commands.id"],
        ),
        sa.PrimaryKeyConstraint("command_id", "sequence"),
    )
    op.create_table(
        "runtime_state",
        sa.Column("thing_id", sa.String(length=32), nullable=False),
        sa.Column("observation_id", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["runtime_observations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["thing_id"],
            ["registry_devices.id"],
        ),
        sa.PrimaryKeyConstraint("thing_id"),
    )


def downgrade() -> None:
    # Development rollback removes runtime data, but preserves registered models/devices.
    op.execute(
        sa.text("""
        UPDATE registry_devices SET lifecycle_status = 'commissioning', revision = revision + 1
        WHERE lifecycle_status = 'active' AND id IN (SELECT thing_id FROM runtime_bindings)
    """)
    )
    op.drop_table("runtime_state")
    op.drop_table("runtime_receipts")
    op.drop_index(op.f("ix_runtime_observations_thing_id"), table_name="runtime_observations")
    op.drop_table("runtime_observations")
    op.drop_index(op.f("ix_runtime_commands_thing_id"), table_name="runtime_commands")
    op.drop_index(op.f("ix_runtime_commands_edge_id"), table_name="runtime_commands")
    op.drop_index(op.f("ix_runtime_commands_domain_ref"), table_name="runtime_commands")
    op.drop_table("runtime_commands")
    op.drop_index(op.f("ix_runtime_bindings_edge_id"), table_name="runtime_bindings")
    op.drop_index(op.f("ix_runtime_bindings_domain_ref"), table_name="runtime_bindings")
    op.drop_table("runtime_bindings")
    op.drop_index(op.f("ix_runtime_edges_domain_ref"), table_name="runtime_edges")
    op.drop_table("runtime_edges")
