from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import AuditLog, KantataUser
from ..deps import current_user

router = APIRouter()

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))

PAGE_SIZE = 50


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {value}") from e


@router.get("/audit", response_class=HTMLResponse)
async def list_audit(
    request: Request,
    user: Annotated[KantataUser, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    action: str | None = Query(None),
    actor_email: str | None = Query(None),
    time_entry_id: int | None = Query(None),
    page: int = Query(1, ge=1),
):
    conditions = []
    if (dt := _parse_date(date_from)) is not None:
        conditions.append(AuditLog.at >= dt)
    if (dt := _parse_date(date_to)) is not None:
        conditions.append(AuditLog.at <= dt)
    if action:
        conditions.append(AuditLog.action == action)
    if actor_email:
        conditions.append(AuditLog.actor_email.ilike(f"%{actor_email}%"))
    if time_entry_id:
        conditions.append(AuditLog.time_entry_id == time_entry_id)

    base = select(AuditLog)
    if conditions:
        base = base.where(*conditions)

    total = await session.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    ) or 0
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)

    rows = (
        await session.execute(
            base.order_by(AuditLog.at.desc()).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
        )
    ).scalars().all()

    return TEMPLATES.TemplateResponse(
        request,
        "audit_list.html",
        {
            "user": user,
            "rows": rows,
            "filters": {
                "date_from": date_from or "",
                "date_to": date_to or "",
                "action": action or "",
                "actor_email": actor_email or "",
                "time_entry_id": time_entry_id or "",
            },
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "retention_years": 5,
        },
    )
