from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from agenticiot.database import Base


class NodeCredential(Base):
    __tablename__ = "node_credentials"
    node_id: Mapped[str] = mapped_column(ForeignKey("runtime_edges.id"), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class NodeSession(Base):
    __tablename__ = "node_sessions"
    node_id: Mapped[str] = mapped_column(ForeignKey("runtime_edges.id"), primary_key=True)
    epoch: Mapped[int] = mapped_column(BigInteger, default=0)
    owner_instance_id: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
