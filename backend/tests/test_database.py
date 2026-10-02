import os
from uuid import uuid4

import pytest
from agenticiot.config import Settings
from agenticiot.main import create_app
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, create_engine, text


@pytest.mark.integration
def test_postgres_rejects_lossy_domain_downgrade(monkeypatch):
    url = os.environ.get("AGENTICIOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AGENTICIOT_TEST_DATABASE_URL to a disposable PostgreSQL database")
    monkeypatch.setenv("AGENTICIOT_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    with TestClient(create_app(Settings())) as client:
        assert client.get("/health/ready").status_code == 200
        with pytest.raises(RuntimeError, match="forward-only"):
            command.downgrade(config, "base")
        assert client.get("/health/ready").status_code == 200


@pytest.mark.integration
def test_domain_backfill_preserves_existing_device_identity():
    url = os.environ.get("AGENTICIOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                schema = "migration_" + uuid4().hex
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                config = Config("alembic.ini")
                config.attributes["connection"] = connection
                command.upgrade(config, "0004_adapters")
                metadata = MetaData()
                models = Table("registry_models", metadata, autoload_with=connection)
                devices = Table("registry_devices", metadata, autoload_with=connection)
                model_id, device_id = uuid4().hex, uuid4().hex
                connection.execute(
                    models.insert().values(
                        id=model_id,
                        domain_ref="old:family",
                        key="light",
                        version="1",
                        title="Preserved model",
                        description="",
                        properties={},
                        actions={},
                        events={},
                    )
                )
                connection.execute(
                    devices.insert().values(
                        id=device_id,
                        domain_ref="old:family",
                        model_id=model_id,
                        model_version="1",
                        external_ref="lamp",
                        title="Preserved device",
                        lifecycle_status="commissioning",
                        reachability="unknown",
                        revision=3,
                    )
                )
                command.upgrade(config, "head")
                row = connection.execute(text("SELECT * FROM registry_devices")).mappings().one()
                assert (
                    row["id"] == device_id and row["model_id"] == model_id and row["revision"] == 3
                )
                assert row["domain_id"] != "old:family"
                assert (
                    connection.execute(text("SELECT domain_id FROM registry_models")).scalar_one()
                    == row["domain_id"]
                )
                alias = connection.execute(text("SELECT * FROM access_aliases")).mappings().one()
                assert (
                    alias["external_ref"] == "old:family" and alias["domain_id"] == row["domain_id"]
                )
                assert (
                    connection.execute(text("SELECT count(*) FROM access_grants")).scalar_one() == 0
                )
            finally:
                # Roll back only this test's transactional schema and fixture rows.
                transaction.rollback()
    finally:
        engine.dispose()
