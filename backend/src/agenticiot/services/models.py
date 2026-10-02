from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agenticiot.database import Base


class ServiceConnection(Base):
    __tablename__ = "service_connections"
    __table_args__ = (UniqueConstraint("domain_id", "name", name="uq_service_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_id: Mapped[str] = mapped_column(ForeignKey("access_domains.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("runtime_edges.id"))
    name: Mapped[str] = mapped_column(String(64))
    local_ref: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    declared_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Invocation(Base):
    __tablename__ = "service_invocations"
    __table_args__ = (
        UniqueConstraint("scope", "key_hash", name="uq_invocation_key"),
        Index("ix_invocation_deadline", "deadline"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain_id: Mapped[str] = mapped_column(ForeignKey("access_domains.id"), index=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("service_connections.id"))
    scope: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str | None] = mapped_column(String(64))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    hmac_key_version: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(20))
    owner_instance_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage: Mapped[dict | None] = mapped_column(JSONB)
    late_report: Mapped[dict | None] = mapped_column(JSONB)
