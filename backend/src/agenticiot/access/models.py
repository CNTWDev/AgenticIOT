from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agenticiot.database import Base


class Domain(Base):
    __tablename__ = "access_domains"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DomainAlias(Base):
    __tablename__ = "access_aliases"
    __table_args__ = (
        UniqueConstraint("issuer", "client_id", "external_ref", name="uq_domain_alias"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(200))
    client_id: Mapped[str] = mapped_column(String(200))
    external_ref: Mapped[str] = mapped_column(String(200))
    domain_id: Mapped[str] = mapped_column(ForeignKey("access_domains.id"), index=True)


class DomainGrant(Base):
    __tablename__ = "access_grants"
    __table_args__ = (UniqueConstraint("alias_id", "subject_ref", name="uq_grant_subject"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    alias_id: Mapped[str] = mapped_column(ForeignKey("access_aliases.id"), index=True)
    # Exact subject, never an implicit wildcard; human and service subjects use the same rule.
    subject_ref: Mapped[str] = mapped_column(String(200))
    permissions: Mapped[list[str]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
