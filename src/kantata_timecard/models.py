from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class KantataUser(Base):
    __tablename__ = "kantata_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    kantata_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    token: Mapped[OAuthToken | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("kantata_users.id", ondelete="CASCADE"), unique=True
    )
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[KantataUser] = relationship(back_populates="token")


class AuditLog(Base):
    """
    Append-only audit log of every adjustment made through this app.

    Retention policy: 5 years. Use `kantata-tc audit purge` (defaults to
    1825 days) on a schedule to enforce. Actor details are denormalized at
    write-time so the record stays meaningful even if the KantataUser row
    is later deleted.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("kantata_users.id", ondelete="SET NULL"), index=True
    )

    actor_kantata_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), index=True)
    actor_name: Mapped[str | None] = mapped_column(String(255))

    action: Mapped[str] = mapped_column(String(32), index=True)
    time_entry_id: Mapped[int] = mapped_column(BigInteger, index=True)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(String(500))

    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))

    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
