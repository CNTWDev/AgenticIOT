from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agenticiot.database import Base


class EdgeNode(Base):
    __tablename__ = "runtime_edges"
    __table_args__ = (UniqueConstraint("domain_id", "edge_ref", name="uq_edge_identity"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_ref: Mapped[str] = mapped_column(
        "domain_id", String(200), ForeignKey("access_domains.id")
    )
    edge_ref: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(40))
    adapters: Mapped[list[str]] = mapped_column(JSONB, default=lambda: ["virtual-light-v1"])
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0)


class Binding(Base):
    __tablename__ = "runtime_bindings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_ref: Mapped[str] = mapped_column(
        "domain_id", String(200), ForeignKey("access_domains.id")
    )
    thing_id: Mapped[str] = mapped_column(ForeignKey("registry_devices.id"), unique=True)
    edge_id: Mapped[str] = mapped_column(ForeignKey("runtime_edges.id"), index=True)
    adapter: Mapped[str] = mapped_column(String(40), default="virtual-light-v1")


class Command(Base):
    __tablename__ = "runtime_commands"
    __table_args__ = (
        UniqueConstraint("domain_id", "subject_ref", "idempotency_key", name="uq_command_key"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_ref: Mapped[str] = mapped_column(
        "domain_id", String(200), ForeignKey("access_domains.id")
    )
    subject_ref: Mapped[str] = mapped_column(String(200))
    thing_id: Mapped[str] = mapped_column(ForeignKey("registry_devices.id"), index=True)
    binding_id: Mapped[str] = mapped_column(ForeignKey("runtime_bindings.id"))
    edge_id: Mapped[str] = mapped_column(ForeignKey("runtime_edges.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(30), default="accepted")
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    trace_id: Mapped[str] = mapped_column(String(32))
    result_hash: Mapped[str | None] = mapped_column(String(64))
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    dispatch_sequence: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    barrier_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[dict | None] = mapped_column(JSONB)


class Receipt(Base):
    __tablename__ = "runtime_receipts"
    command_id: Mapped[str] = mapped_column(ForeignKey("runtime_commands.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(30))
    executor_ref: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    trace_id: Mapped[str] = mapped_column(String(32))


class Observation(Base):
    __tablename__ = "runtime_observations"
    __table_args__ = (
        UniqueConstraint("edge_id", "source_sequence", name="uq_observation_sequence"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_ref: Mapped[str] = mapped_column(
        "domain_id", String(200), ForeignKey("access_domains.id")
    )
    thing_id: Mapped[str] = mapped_column(ForeignKey("registry_devices.id"), index=True)
    edge_id: Mapped[str] = mapped_column(ForeignKey("runtime_edges.id"))
    command_id: Mapped[str | None] = mapped_column(ForeignKey("runtime_commands.id"))
    source_sequence: Mapped[int] = mapped_column(BigInteger)
    source_ref: Mapped[str] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_hash: Mapped[str] = mapped_column(String(64))
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StateProjection(Base):
    __tablename__ = "runtime_state"
    thing_id: Mapped[str] = mapped_column(ForeignKey("registry_devices.id"), primary_key=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey("runtime_observations.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_ref: Mapped[str] = mapped_column(String(200))
    source_sequence: Mapped[int] = mapped_column(BigInteger)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)


# Preserve existing physical index names across the non-destructive domain migration.
for _model in (EdgeNode, Binding, Command):
    Index(f"ix_{_model.__tablename__}_domain_ref", _model.domain_ref)

Index("ix_command_dispatch", Command.edge_id, Command.stage, Command.created_at, Command.id)
Index("ix_command_blocked", Command.edge_id, Command.blocked)
Index("ix_observation_received", Observation.received_at)
Index("ix_command_reconciliation_observation", Command.resolution["observation_id"].astext)
Index("ix_state_observation", StateProjection.observation_id)
