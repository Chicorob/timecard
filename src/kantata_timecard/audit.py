from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog


async def record(
    session: AsyncSession,
    *,
    user_id: int | None,
    action: str,
    time_entry_id: int,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    note: str | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            time_entry_id=time_entry_id,
            before=before,
            after=after,
            note=note,
        )
    )
