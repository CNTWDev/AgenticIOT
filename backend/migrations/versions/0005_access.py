"""Backfill stable resource domains without discarding existing resource history."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_access"
down_revision = "0004_adapters"
branch_labels = None
depends_on = None
TABLES = (
    "registry_models",
    "registry_devices",
    "registry_audit",
    "runtime_edges",
    "runtime_bindings",
    "runtime_commands",
    "runtime_observations",
)


def upgrade():
    op.create_table(
        "access_domains",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "access_aliases",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer", sa.String(200), nullable=False),
        sa.Column("client_id", sa.String(200), nullable=False),
        sa.Column("external_ref", sa.String(200), nullable=False),
        sa.Column("domain_id", sa.String(32), sa.ForeignKey("access_domains.id"), nullable=False),
        sa.UniqueConstraint("issuer", "client_id", "external_ref", name="uq_domain_alias"),
    )
    op.create_index("ix_access_aliases_domain_id", "access_aliases", ["domain_id"])
    op.create_table(
        "access_grants",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("alias_id", sa.String(32), sa.ForeignKey("access_aliases.id"), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("permissions", JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("alias_id", "subject_ref", name="uq_grant_subject"),
    )
    op.create_index("ix_access_grants_alias_id", "access_grants", ["alias_id"])
    bind = op.get_bind()
    refs = set()
    for table in TABLES:
        refs.update(bind.execute(sa.text(f"SELECT DISTINCT domain_ref FROM {table}")).scalars())
    op.drop_constraint("fk_device_model_domain", "registry_devices", type_="foreignkey")
    for external in refs:
        domain, alias = uuid4().hex, uuid4().hex
        bind.execute(
            sa.text("INSERT INTO access_domains(id,title) VALUES (:id,:title)"),
            {"id": domain, "title": external[:160]},
        )
        bind.execute(
            sa.text("INSERT INTO access_aliases VALUES (:id,:issuer,:client,:ref,:domain)"),
            {
                "id": alias,
                "issuer": "urn:agenticiot:development",
                "client": "configured-client",
                "ref": external,
                "domain": domain,
            },
        )
        for table in TABLES:
            bind.execute(
                sa.text(f"UPDATE {table} SET domain_ref=:id WHERE domain_ref=:ref"),
                {"id": domain, "ref": external},
            )
    for table in TABLES:
        op.alter_column(table, "domain_ref", new_column_name="domain_id")
        op.create_foreign_key(f"fk_{table}_domain", table, "access_domains", ["domain_id"], ["id"])
    op.create_foreign_key(
        "fk_device_model_domain",
        "registry_devices",
        "registry_models",
        ["domain_id", "model_id"],
        ["domain_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade():
    # Multiple external aliases can refer to one domain; choosing one would lose identity.
    raise RuntimeError("Domain backfill is forward-only; restore a verified pre-migration backup")
