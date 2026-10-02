"""Device models, devices and atomic registry audit records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_registry"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_models",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("domain_ref", sa.String(200), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("properties", postgresql.JSONB(), nullable=False),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("domain_ref", "key", "version", name="uq_model_version"),
        sa.UniqueConstraint("domain_ref", "id", name="uq_model_domain_id"),
    )
    op.create_index("ix_registry_models_domain_ref", "registry_models", ["domain_ref"])
    op.create_table(
        "registry_devices",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("domain_ref", sa.String(200), nullable=False),
        sa.Column("model_id", sa.String(32), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("external_ref", sa.String(200), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("space_ref", sa.String(200)),
        sa.Column("lifecycle_status", sa.String(20), nullable=False),
        sa.Column("reachability", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("domain_ref", "external_ref", name="uq_device_external_ref"),
        sa.ForeignKeyConstraint(
            ["domain_ref", "model_id"],
            ["registry_models.domain_ref", "registry_models.id"],
            name="fk_device_model_domain",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_registry_devices_domain_ref", "registry_devices", ["domain_ref"])
    op.create_table(
        "registry_audit",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("domain_ref", sa.String(200), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.String(32), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_registry_audit_domain_ref", "registry_audit", ["domain_ref"])
    op.create_index("ix_registry_audit_resource_id", "registry_audit", ["resource_id"])


def downgrade() -> None:
    op.drop_table("registry_audit")
    op.drop_table("registry_devices")
    op.drop_table("registry_models")
