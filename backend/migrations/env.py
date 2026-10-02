from agenticiot import events  # noqa: F401
from agenticiot.access import models as access_models  # noqa: F401
from agenticiot.config import Settings
from agenticiot.database import Base, build_engine
from agenticiot.nodes import models as node_models  # noqa: F401
from agenticiot.registry import models  # noqa: F401
from agenticiot.runtime import models as runtime_models  # noqa: F401
from agenticiot.services import models as service_models  # noqa: F401
from alembic import context

target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=Settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    connection = context.config.attributes["connection"]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = build_engine(Settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
