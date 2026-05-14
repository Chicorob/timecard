from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ... import audit as audit_log
from ...db import get_session
from ...kantata_client import KantataAPIError, KantataClient
from ...models import KantataUser
from ..deps import current_user, kantata_client_for_user

router = APIRouter()

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def _minutes_from_hours(hours_str: str | None) -> int | None:
    if not hours_str:
        return None
    try:
        return int(round(float(hours_str) * 60))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid hours value: {hours_str}") from e


def _format_hours(minutes: int | None) -> str:
    if minutes is None:
        return ""
    return f"{minutes / 60:.2f}"


@router.get("/entries", response_class=HTMLResponse)
async def list_entries(
    request: Request,
    user: KantataUser = Depends(current_user),
    client: KantataClient = Depends(kantata_client_for_user),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    workspace_id: int | None = Query(None),
    user_id: int | None = Query(None),
):
    entries = await client.list_time_entries(
        user_ids=[user_id] if user_id else None,
        workspace_id=workspace_id,
        date_from=date_from,
        date_to=date_to,
        max_items=500,
    )
    return TEMPLATES.TemplateResponse(
        request,
        "entries_list.html",
        {
            "user": user,
            "entries": entries,
            "format_hours": _format_hours,
            "filters": {
                "date_from": date_from or "",
                "date_to": date_to or "",
                "workspace_id": workspace_id or "",
                "user_id": user_id or "",
            },
        },
    )


@router.get("/entries/{entry_id}/edit", response_class=HTMLResponse)
async def edit_entry_form(
    request: Request,
    entry_id: int,
    user: KantataUser = Depends(current_user),
    client: KantataClient = Depends(kantata_client_for_user),
):
    entry = await client.get_time_entry(entry_id)
    return TEMPLATES.TemplateResponse(
        request,
        "entry_edit.html",
        {"user": user, "entry": entry, "format_hours": _format_hours},
    )


@router.post("/entries/{entry_id}")
async def update_entry(
    entry_id: int,
    user: Annotated[KantataUser, Depends(current_user)],
    client: Annotated[KantataClient, Depends(kantata_client_for_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    hours: str = Form(""),
    date_performed: str = Form(""),
    notes: str = Form(""),
    story_id: str = Form(""),
    workspace_id: str = Form(""),
    billable: str = Form("false"),
):
    before = await client.get_time_entry(entry_id)

    fields: dict = {
        "time_in_minutes": _minutes_from_hours(hours) if hours else None,
        "date_performed": date_performed or None,
        "notes": notes if notes != "" else None,
        "story_id": int(story_id) if story_id else None,
        "workspace_id": int(workspace_id) if workspace_id else None,
        "billable": billable.lower() in {"true", "on", "1", "yes"},
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    try:
        after = await client.update_time_entry(entry_id, fields)
    except KantataAPIError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=str(e)) from e

    await audit_log.record(
        session,
        user_id=user.id,
        action="update",
        time_entry_id=entry_id,
        before={k: before.get(k) for k in fields.keys()},
        after={k: after.get(k) for k in fields.keys()},
    )
    await session.commit()

    return RedirectResponse(url="/entries", status_code=303)


@router.post("/entries/{entry_id}/delete")
async def delete_entry(
    entry_id: int,
    user: Annotated[KantataUser, Depends(current_user)],
    client: Annotated[KantataClient, Depends(kantata_client_for_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    before = await client.get_time_entry(entry_id)
    try:
        await client.delete_time_entry(entry_id)
    except KantataAPIError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=str(e)) from e

    await audit_log.record(
        session,
        user_id=user.id,
        action="delete",
        time_entry_id=entry_id,
        before=before,
    )
    await session.commit()
    return RedirectResponse(url="/entries", status_code=303)


@router.post("/entries/bulk")
async def bulk_update(
    user: Annotated[KantataUser, Depends(current_user)],
    client: Annotated[KantataClient, Depends(kantata_client_for_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ids: str = Form(...),
    action: str = Form(...),  # "update" | "delete"
    hours: str = Form(""),
    date_performed: str = Form(""),
    notes: str = Form(""),
    story_id: str = Form(""),
    workspace_id: str = Form(""),
    billable: str = Form(""),
):
    entry_ids = [int(x) for x in ids.split(",") if x.strip()]
    if not entry_ids:
        raise HTTPException(status_code=400, detail="no IDs provided")

    if action == "delete":
        await client.delete_time_entries(entry_ids)
        for entry_id in entry_ids:
            await audit_log.record(
                session, user_id=user.id, action="delete", time_entry_id=entry_id, note="bulk"
            )
        await session.commit()
        return RedirectResponse(url="/entries", status_code=303)

    fields: dict = {}
    if hours:
        fields["time_in_minutes"] = _minutes_from_hours(hours)
    if date_performed:
        fields["date_performed"] = date_performed
    if notes:
        fields["notes"] = notes
    if story_id:
        fields["story_id"] = int(story_id)
    if workspace_id:
        fields["workspace_id"] = int(workspace_id)
    if billable:
        fields["billable"] = billable.lower() in {"true", "on", "1", "yes"}

    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")

    errors: list[str] = []
    for entry_id in entry_ids:
        try:
            after = await client.update_time_entry(entry_id, fields)
            await audit_log.record(
                session,
                user_id=user.id,
                action="update",
                time_entry_id=entry_id,
                after={k: after.get(k) for k in fields.keys()},
                note="bulk",
            )
        except KantataAPIError as e:
            errors.append(f"{entry_id}: {e}")
    await session.commit()

    if errors:
        raise HTTPException(status_code=207, detail={"errors": errors, "applied": len(entry_ids) - len(errors)})
    return RedirectResponse(url="/entries", status_code=303)
