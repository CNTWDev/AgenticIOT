from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agenticiot.database import Base


class DeviceModel(Base):
    __tablename__ = "registry_models"
    __table_args__ = (
        UniqueConstraint("domain_id", "key", "version", name="uq_model_version"),
        UniqueConstraint("domain_id", "id", name="uq_model_domain_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_ref: Mapped[str] = mapped_column(
        "domain_id", String(200), ForeignKey("access_domains.id")
    )
    key: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(2000))
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    events: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    __tablename__ = "registry_devices"
    __table_args__ = (
        UniqueConstraint("domain_id", "external_ref", name="uq_device_external_ref"),
        ForeignKeyConstraint(
            ["domain_id", "model_id"],
            ["registry_models.domain_id", "registry_models.id"],
            name="fk_device_model_domain",
            ondelete="RESTRICT",
        ),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_ref: Mapped[str] = mapped_column(
        "domain_id", String(200), ForeignKey("access_domains.id")
    )
    model_id: Mapped[str] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(40))
    external_ref: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(160))
    space_ref: Mapped[str | None] = mapped_column(String(200))
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="commissioning")
    reachability: Mapped[str] = mapped_column(String(20), default="unknown")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"version_id_col": revision}


class RegistryAudit(Base):
    __tablename__ = "registry_audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_ref: Mapped[str] = mapped_column(
        "domain_id", String(200), ForeignKey("access_domains.id")
    )
    subject_ref: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(32), index=True)
    trace_id: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Preserve existing physical index names across the non-destructive domain migration.
for _model in (DeviceModel, Device, RegistryAudit):
    Index(f"ix_{_model.__tablename__}_domain_ref", _model.domain_ref)
