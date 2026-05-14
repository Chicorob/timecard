from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, KantataUser

RETENTION_DAYS = 5 * 365  # 5 years


async def record(
    session: AsyncSession,
    *,
    user: KantataUser | None,
    action: str,
    time_entry_id: int,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    note: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append an audit-log row. Caller is responsible for committing the session."""
    session.add(
        AuditLog(
            user_id=user.id if user else None,
            actor_kantata_user_id=user.kantata_user_id if user else None,
            actor_email=user.email if user else None,
            actor_name=user.full_name if user else None,
            action=action,
            time_entry_id=time_entry_id,
            before=before,
            after=after,
            note=note,
            ip_address=ip_address,
            user_agent=(user_agent[:500] if user_agent else None),
        )
    )


async def purge_older_than(session: AsyncSession, days: int = RETENTION_DAYS) -> int:
    """Delete audit-log rows older than `days` days. Returns the row count deleted."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(delete(AuditLog).where(AuditLog.at < cutoff))
    await session.commit()
    return result.rowcount or 0
