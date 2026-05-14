from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from .. import audit as audit_log
from ..db import init_db, sessionmaker_
from ..kantata_client import KantataAPIError, KantataClient
from ..models import AuditLog

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Bulk admin CLI for Kantata time-card adjustments.",
    pretty_exceptions_show_locals=False,
)
console = Console()


def _get_token() -> str:
    token = os.environ.get("KANTATA_ADMIN_TOKEN")
    if not token:
        console.print(
            "[red]KANTATA_ADMIN_TOKEN is not set.[/red]\n"
            "Reveal it in Kantata: Settings -> API -> 'Your OAuth Token', then set the env var "
            "(in PowerShell: $env:KANTATA_ADMIN_TOKEN = '...')."
        )
        raise typer.Exit(code=2)
    return token


def _api_base() -> str:
    return os.environ.get("KANTATA_API_BASE", "https://api.mavenlink.com/api/v1")


def _run(coro):
    return asyncio.run(coro)


def _print_entries(entries: list[dict]) -> None:
    table = Table(show_lines=False, header_style="bold")
    table.add_column("ID")
    table.add_column("Date")
    table.add_column("User")
    table.add_column("Project")
    table.add_column("Task")
    table.add_column("Hours", justify="right")
    table.add_column("Billable")
    table.add_column("Notes", overflow="fold", max_width=40)

    for e in entries:
        inc = e.get("__includes__", {})
        u = inc.get("users", {}).get(str(e.get("user_id")), {})
        w = inc.get("workspaces", {}).get(str(e.get("workspace_id")), {})
        s = inc.get("stories", {}).get(str(e.get("story_id")), {})
        minutes = e.get("time_in_minutes") or 0
        table.add_row(
            str(e.get("id")),
            e.get("date_performed", "") or "",
            u.get("full_name", "—"),
            w.get("title", "—"),
            s.get("title", "—"),
            f"{minutes/60:.2f}",
            "yes" if e.get("billable") else "no",
            (e.get("notes") or "")[:200],
        )
    console.print(table)
    console.print(f"[dim]{len(entries)} entries[/dim]")


@app.command("list")
def list_entries(
    date_from: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, typer.Option("--to", help="YYYY-MM-DD")] = None,
    user_id: Annotated[int | None, typer.Option(help="Filter by user ID")] = None,
    workspace_id: Annotated[int | None, typer.Option(help="Filter by project ID")] = None,
    limit: Annotated[int, typer.Option(help="Max entries to return")] = 200,
):
    """List time entries matching the given filters."""
    async def _run_it():
        async with KantataClient(_get_token(), api_base=_api_base()) as client:
            entries = await client.list_time_entries(
                user_ids=[user_id] if user_id else None,
                workspace_id=workspace_id,
                date_from=date_from,
                date_to=date_to,
                max_items=limit,
            )
            _print_entries(entries)

    _run(_run_it())


@app.command("show")
def show(entry_id: int):
    """Show a single time entry by ID."""
    async def _run_it():
        async with KantataClient(_get_token(), api_base=_api_base()) as client:
            entry = await client.get_time_entry(entry_id)
            _print_entries([entry])

    _run(_run_it())


@app.command("update")
def update(
    entry_id: int,
    hours: Annotated[float | None, typer.Option(help="Hours (e.g. 2.5)")] = None,
    date_performed: Annotated[str | None, typer.Option("--date", help="YYYY-MM-DD")] = None,
    notes: Annotated[str | None, typer.Option()] = None,
    story_id: Annotated[int | None, typer.Option(help="Task ID")] = None,
    workspace_id: Annotated[int | None, typer.Option(help="Project ID")] = None,
    billable: Annotated[bool | None, typer.Option()] = None,
    user_id: Annotated[int | None, typer.Option(help="Reassign to user (admin only)")] = None,
):
    """Update a single time entry."""
    fields: dict = {}
    if hours is not None:
        fields["time_in_minutes"] = int(round(hours * 60))
    if date_performed:
        fields["date_performed"] = date_performed
    if notes is not None:
        fields["notes"] = notes
    if story_id is not None:
        fields["story_id"] = story_id
    if workspace_id is not None:
        fields["workspace_id"] = workspace_id
    if billable is not None:
        fields["billable"] = billable
    if user_id is not None:
        fields["user_id"] = user_id

    if not fields:
        console.print("[yellow]Nothing to update — pass at least one field option.[/yellow]")
        raise typer.Exit(code=1)

    async def _run_it():
        async with KantataClient(_get_token(), api_base=_api_base()) as client:
            updated = await client.update_time_entry(entry_id, fields)
            console.print(f"[green]Updated entry {entry_id}.[/green]")
            _print_entries([updated])

    _run(_run_it())


@app.command("delete")
def delete(
    entry_ids: Annotated[list[int], typer.Argument(help="One or more entry IDs")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
):
    """Delete one or more time entries (bulk-deletes in batches of 100)."""
    if not yes:
        if not typer.confirm(f"Permanently delete {len(entry_ids)} time entries?"):
            raise typer.Abort()

    async def _run_it():
        async with KantataClient(_get_token(), api_base=_api_base()) as client:
            for i in range(0, len(entry_ids), 100):
                batch = entry_ids[i : i + 100]
                await client.delete_time_entries(batch)
                console.print(f"[green]Deleted {len(batch)} entries.[/green]")

    _run(_run_it())


@app.command("bulk-move")
def bulk_move(
    from_task: Annotated[int, typer.Option("--from-task", help="Source task (story) ID")],
    to_task: Annotated[int, typer.Option("--to-task", help="Destination task (story) ID")],
    to_workspace: Annotated[
        int | None, typer.Option("--to-workspace", help="Also move to a different project")
    ] = None,
    date_from: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, typer.Option("--to", help="YYYY-MM-DD")] = None,
    user_id: Annotated[int | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show matches, don't update")] = False,
):
    """Move all matching time entries to a different task (and optionally project)."""
    async def _run_it():
        async with KantataClient(_get_token(), api_base=_api_base()) as client:
            entries = await client.list_time_entries(
                user_ids=[user_id] if user_id else None,
                date_from=date_from,
                date_to=date_to,
            )
            matches = [e for e in entries if int(e.get("story_id") or -1) == from_task]
            console.print(f"[bold]Found {len(matches)} entries on task {from_task}.[/bold]")
            _print_entries(matches)

            if dry_run or not matches:
                return
            if not typer.confirm(f"Move all {len(matches)} entries to task {to_task}?"):
                raise typer.Abort()

            fields: dict = {"story_id": to_task}
            if to_workspace is not None:
                fields["workspace_id"] = to_workspace

            ok = err = 0
            for e in matches:
                try:
                    await client.update_time_entry(int(e["id"]), fields)
                    ok += 1
                except KantataAPIError as exc:
                    console.print(f"[red]entry {e['id']} failed: {exc}[/red]")
                    err += 1
            console.print(f"[green]Moved {ok}.[/green] [red]Failed {err}.[/red]")

    _run(_run_it())


@app.command("bulk-shift-dates")
def bulk_shift_dates(
    days: Annotated[int, typer.Option(help="Number of days to shift (negative = earlier)")],
    date_from: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, typer.Option("--to", help="YYYY-MM-DD")] = None,
    user_id: Annotated[int | None, typer.Option()] = None,
    workspace_id: Annotated[int | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
):
    """Shift the date_performed of all matching entries by N days."""
    async def _run_it():
        async with KantataClient(_get_token(), api_base=_api_base()) as client:
            entries = await client.list_time_entries(
                user_ids=[user_id] if user_id else None,
                workspace_id=workspace_id,
                date_from=date_from,
                date_to=date_to,
            )
            console.print(f"[bold]Found {len(entries)} entries to shift by {days} days.[/bold]")
            _print_entries(entries)

            if dry_run or not entries:
                return
            if not typer.confirm(f"Shift dates on all {len(entries)} entries by {days} days?"):
                raise typer.Abort()

            ok = err = 0
            for e in entries:
                try:
                    current = datetime.strptime(e["date_performed"], "%Y-%m-%d").date()
                    new_date = current + timedelta(days=days)
                    await client.update_time_entry(int(e["id"]), {"date_performed": new_date.isoformat()})
                    ok += 1
                except (KantataAPIError, ValueError, KeyError) as exc:
                    console.print(f"[red]entry {e.get('id')} failed: {exc}[/red]")
                    err += 1
            console.print(f"[green]Shifted {ok}.[/green] [red]Failed {err}.[/red]")

    _run(_run_it())


audit_app = typer.Typer(
    no_args_is_help=True, help="View and manage the audit log (5-year retention)."
)
app.add_typer(audit_app, name="audit")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as e:
        console.print(f"[red]invalid ISO date/time: {value}[/red]")
        raise typer.Exit(code=1) from e


@audit_app.command("list")
def audit_list_cmd(
    date_from: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, typer.Option("--to", help="YYYY-MM-DD")] = None,
    action: Annotated[
        str | None, typer.Option(help="Filter by action (update, delete, create)")
    ] = None,
    actor: Annotated[
        str | None, typer.Option(help="Filter where actor email contains this substring")
    ] = None,
    time_entry_id: Annotated[int | None, typer.Option(help="Filter by Kantata time entry ID")] = None,
    limit: Annotated[int, typer.Option(help="Max rows to return")] = 100,
):
    """List audit-log records (most recent first)."""
    async def _run_it():
        await init_db()
        async with sessionmaker_()() as session:
            stmt = select(AuditLog).order_by(AuditLog.at.desc())
            if (dt := _parse_iso(date_from)) is not None:
                stmt = stmt.where(AuditLog.at >= dt)
            if (dt := _parse_iso(date_to)) is not None:
                stmt = stmt.where(AuditLog.at <= dt)
            if action:
                stmt = stmt.where(AuditLog.action == action)
            if actor:
                stmt = stmt.where(AuditLog.actor_email.ilike(f"%{actor}%"))
            if time_entry_id is not None:
                stmt = stmt.where(AuditLog.time_entry_id == time_entry_id)
            stmt = stmt.limit(limit)

            rows = (await session.execute(stmt)).scalars().all()

            table = Table(show_lines=False, header_style="bold")
            table.add_column("When (UTC)")
            table.add_column("Actor")
            table.add_column("Action")
            table.add_column("Entry")
            table.add_column("Note")
            table.add_column("IP")
            for r in rows:
                table.add_row(
                    r.at.strftime("%Y-%m-%d %H:%M") if r.at else "—",
                    r.actor_email or r.actor_name or "—",
                    r.action,
                    str(r.time_entry_id),
                    r.note or "",
                    r.ip_address or "",
                )
            console.print(table)
            console.print(f"[dim]{len(rows)} rows[/dim]")

    _run(_run_it())


@audit_app.command("purge")
def audit_purge_cmd(
    older_than_days: Annotated[
        int,
        typer.Option(
            "--older-than-days",
            help="Records older than this many days are deleted. Default: 1825 (5 years).",
        ),
    ] = audit_log.RETENTION_DAYS,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
):
    """Enforce the 5-year retention policy by deleting old audit records."""
    cutoff = datetime.now() - timedelta(days=older_than_days)
    if not yes and not typer.confirm(
        f"Permanently delete audit records older than {older_than_days} days "
        f"(before {cutoff.date().isoformat()})?"
    ):
        raise typer.Abort()

    async def _run_it():
        await init_db()
        async with sessionmaker_()() as session:
            deleted = await audit_log.purge_older_than(session, days=older_than_days)
            console.print(f"[green]Deleted {deleted} audit record(s).[/green]")

    _run(_run_it())


@app.command("whoami")
def whoami():
    """Verify the configured admin token by calling /users/me."""
    async def _run_it():
        async with KantataClient(_get_token(), api_base=_api_base()) as client:
            me = await client.get_me()
            console.print(
                f"Authenticated as [bold]{me.get('full_name')}[/bold] "
                f"({me.get('email_address') or me.get('email')}) — Kantata user id {me.get('id')}"
            )

    _run(_run_it())


def main():
    try:
        app()
    except KantataAPIError as e:
        console.print(f"[red]Kantata API error:[/red] {e}")
        if e.body:
            console.print(e.body)
        sys.exit(1)


if __name__ == "__main__":
    main()
