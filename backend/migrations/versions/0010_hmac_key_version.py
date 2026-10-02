"""Record HMAC key versions; retain old keys through the metadata window."""

import sqlalchemy as sa
from alembic import op

revision = "0010_hmac_key_version"
down_revision = "0009_invocation_facts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("service_invocations", sa.Column("hmac_key_version", sa.String(16)))


def downgrade():
    op.drop_column("service_invocations", "hmac_key_version")
