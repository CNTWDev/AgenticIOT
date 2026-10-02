from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase

from agenticiot.config import Settings

FOUNDATION_REVISION = "0010_hmac_key_version"


class Base(DeclarativeBase):
    """Shared metadata only; application services own cross-module access."""


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=3,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
    )


def probe_database(engine: Engine) -> bool:
    """Ready only when the database is reachable and the expected schema is applied."""
    with engine.connect() as connection:
        versions = set(
            connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )
    return versions == {FOUNDATION_REVISION}
