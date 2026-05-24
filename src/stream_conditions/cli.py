"""Operational CLI for stream-conditions tasks."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

import typer

from stream_conditions.ingest import DEFAULT_DB, backfill_gauge, fetch_all
from stream_conditions.sources.gauge_registry import list_gauges, register_gauge
from stream_conditions.storage.base import Session
from stream_conditions.storage.sqlite import Database

app = typer.Typer(
    name="stream-conditions",
    help="Stream Conditions operational CLI.",
    no_args_is_help=True,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

_DB_OPTION = typer.Option(
    None,
    "--db",
    help="Path to SQLite database (overrides DB_PATH env var).",
    envvar="DB_PATH",
)


def _db_path(db: Path | None) -> Path:
    return db if db is not None else DEFAULT_DB


# ── Gauge management ───────────────────────────────────────────────────────────

@app.command()
def register(
    site_id: str = typer.Argument(..., help="USGS site ID (e.g. 02334430)"),
    notes: str = typer.Option("", help="Free-text notes to attach to this gauge"),
    db: Path | None = _DB_OPTION,
) -> None:
    """Register a gauge by fetching its metadata from USGS and saving to the DB."""
    from stream_conditions.storage.sqlite import Database

    db_path = _db_path(db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # register_gauge uses InMemoryGaugeStore by default; we persist to SQLite directly.
    gauge = register_gauge(site_id, notes=notes)

    async def _save() -> None:
        async with Database(db_path) as _db:
            await _db.gauges.upsert(gauge)

    asyncio.run(_save())
    typer.echo(
        f"Registered: {gauge.site_id}  {gauge.name}  "
        f"({gauge.latitude:.4f}, {gauge.longitude:.4f})"
    )


@app.command("list-gauges")
def list_gauges_cmd(db: Path | None = _DB_OPTION) -> None:
    """List all gauges registered in the database."""
    db_path = _db_path(db)

    async def _list() -> None:
        async with Database(db_path) as _db:
            gauges = await _db.gauges.list()
        if not gauges:
            typer.echo("No gauges registered. Use `stream-conditions register <site_id>`.")
            return
        for g in gauges:
            typer.echo(
                f"{g.site_id}  {g.name:<50}  "
                f"lat={g.latitude:.4f}  lon={g.longitude:.4f}"
            )

    asyncio.run(_list())


# ── Data collection ────────────────────────────────────────────────────────────

@app.command()
def fetch(db: Path | None = _DB_OPTION) -> None:
    """Fetch current conditions for every registered gauge and write to the DB.

    Idempotent — skips gauges whose snapshot is less than 10 minutes old.
    Exits with code 1 if any gauge fails, 2 if no gauges are registered.
    """
    db_path = _db_path(db)
    results = asyncio.run(fetch_all(db_path))

    if not results:
        typer.echo("No gauges registered.", err=True)
        raise typer.Exit(2)

    failed = [sid for sid, ok in results.items() if not ok]
    for sid, ok in results.items():
        icon = "✓" if ok else "✗"
        typer.echo(f"  {icon} {sid}")

    if failed:
        typer.echo(f"\n{len(failed)} gauge(s) failed.", err=True)
        raise typer.Exit(1)


@app.command()
def backfill(
    site_id: str = typer.Argument(..., help="USGS site ID to backfill"),
    days: int = typer.Option(30, "--days", "-d", help="Number of days of history to pull"),
    db: Path | None = _DB_OPTION,
) -> None:
    """Pull historical USGS + weather data and write snapshots for the past N days.

    The gauge must be registered first (`stream-conditions register <site_id>`).
    Runs are idempotent — timestamps already present are skipped.
    """
    db_path = _db_path(db)
    try:
        written = asyncio.run(backfill_gauge(site_id, days, db_path))
        typer.echo(f"Backfill complete: {written} new snapshots for {site_id}.")
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


# ── Fishing session logging ────────────────────────────────────────────────────

_HATCH_ORDERS = [
    "ephemeroptera",
    "trichoptera",
    "diptera",
    "plecoptera",
    "terrestrial",
    "none",
    "unknown",
]

_HATCH_STAGES = ["emerger", "dun", "spinner", "nymph", "pupa", "adult", "n/a"]


def _pick(label: str, choices: list[str], default_idx: int) -> str:
    """Display a numbered menu and return the chosen item."""
    typer.echo(f"\n{label}:")
    for i, c in enumerate(choices, 1):
        typer.echo(f"  {i}. {c}")
    raw = typer.prompt("Select (number)", default=str(default_idx))
    try:
        idx = int(raw) - 1
        if idx < 0 or idx >= len(choices):
            raise ValueError
        return choices[idx]
    except ValueError:
        typer.echo(
            f"Invalid selection — must be 1–{len(choices)}.", err=True
        )
        raise typer.Exit(1)


def _parse_local_time(prompt_text: str, default_hhmm: str) -> datetime:
    """Prompt for HH:MM, combine with today's date in local time, return UTC."""
    today = date.today()
    while True:
        raw = typer.prompt(prompt_text, default=default_hhmm)
        try:
            t = datetime.strptime(raw, "%H:%M").time()
            return datetime.combine(today, t).astimezone(timezone.utc)
        except ValueError:
            typer.echo("  Enter time as HH:MM (e.g. 14:30).", err=True)


async def _log_session_async(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Gauge selection
    async with Database(db_path) as db:
        gauges = await db.gauges.list()

    if not gauges:
        typer.echo(
            "No gauges registered. "
            "Run `stream-conditions register <site_id>` first.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo("Registered gauges:")
    for i, g in enumerate(gauges, 1):
        typer.echo(f"  {i}. {g.site_id}  {g.name}")

    raw_gauge = typer.prompt("Select gauge (number)")
    try:
        gauge_idx = int(raw_gauge) - 1
        if gauge_idx < 0 or gauge_idx >= len(gauges):
            raise ValueError
        gauge = gauges[gauge_idx]
    except ValueError:
        typer.echo(
            f"Invalid selection — must be 1–{len(gauges)}.", err=True
        )
        raise typer.Exit(1)

    # 2 & 3. Session times
    started_at = _parse_local_time("Start time (HH:MM, local)", "08:00")
    ended_at = _parse_local_time("End time   (HH:MM, local)", "12:00")

    if ended_at <= started_at:
        typer.echo("End time must be after start time.", err=True)
        raise typer.Exit(1)

    # 4–6. Hatch details
    hatch_order = _pick("Hatch order", _HATCH_ORDERS, len(_HATCH_ORDERS))
    hatch_stage = _pick("Hatch stage", _HATCH_STAGES, len(_HATCH_STAGES))

    raw_hi = typer.prompt(
        "\nHatch intensity (0=none, 1=sparse, 2=moderate, 3=heavy)", default="0"
    )
    try:
        hatch_intensity = int(raw_hi)
        if hatch_intensity not in (0, 1, 2, 3):
            raise ValueError
    except ValueError:
        typer.echo("Hatch intensity must be 0, 1, 2, or 3.", err=True)
        raise typer.Exit(1)

    # 7 & 8. Catch details
    raw_fc = typer.prompt("\nFish count", default="0")
    try:
        fish_count = int(raw_fc)
        if fish_count < 0:
            raise ValueError
    except ValueError:
        typer.echo("Fish count must be a non-negative integer.", err=True)
        raise typer.Exit(1)

    fish_species = (
        typer.prompt(
            "Fish species (e.g. rainbow, brown, brook)", default=""
        )
        or None
    )

    # 9. Notes
    typer.echo("\nNotes (one line at a time; empty line to finish):")
    note_lines: list[str] = []
    while True:
        line = typer.prompt("...", default="", show_default=False)
        if not line:
            break
        note_lines.append(line)
    notes = "\n".join(note_lines)

    # Persist and query overlapping snapshots in one connection
    session = Session(
        site_id=gauge.site_id,
        started_at=started_at,
        ended_at=ended_at,
        hatch_order=hatch_order,
        hatch_stage=hatch_stage,
        hatch_intensity=hatch_intensity,
        fish_count=fish_count,
        fish_species=fish_species,
        notes=notes,
    )

    async with Database(db_path) as db:
        session_id = await db.sessions.insert(session)
        snapshots = await db.snapshots.get_range(gauge.site_id, started_at, ended_at)

    typer.echo(f"\nSession #{session_id} logged for {gauge.site_id}.")

    if not snapshots:
        typer.echo("No snapshots found for this time window.")
        return

    # Conditions table
    n = len(snapshots)
    typer.echo(f"\nConditions during session ({n} snapshot{'s' if n != 1 else ''}):")
    hdr = (
        f"{'Time (UTC)':<20} {'Discharge cfs':>14} {'Stage ft':>9}"
        f" {'Water °C':>9} {'Air °C':>7} {'Pressure hPa':>13}"
    )
    typer.echo(hdr)
    typer.echo("─" * len(hdr))
    for s in snapshots:
        ts = s.fetched_at.strftime("%Y-%m-%d %H:%M")
        discharge = f"{s.discharge_cfs:.1f}" if s.discharge_cfs is not None else "—"
        stage = f"{s.gauge_height_ft:.2f}" if s.gauge_height_ft is not None else "—"
        water_t = f"{s.water_temp_c:.1f}" if s.water_temp_c is not None else "—"
        air_t = f"{s.air_temp_c:.1f}" if s.air_temp_c is not None else "—"
        pressure = f"{s.pressure_hpa:.1f}" if s.pressure_hpa is not None else "—"
        typer.echo(
            f"{ts:<20} {discharge:>14} {stage:>9}"
            f" {water_t:>9} {air_t:>7} {pressure:>13}"
        )


@app.command("log-session")
def log_session(
    db: Path | None = _DB_OPTION,
) -> None:
    """Interactively record a fishing session and display matching conditions."""
    asyncio.run(_log_session_async(_db_path(db)))


# ── Prediction ─────────────────────────────────────────────────────────────────

@app.command()
def predict(
    site_id: str = typer.Argument(..., help="USGS gauge site ID"),
    top_n: int = typer.Option(5, help="Number of upcoming windows to surface"),
) -> None:
    """Predict the next optimal fly-fishing windows for a gauge."""
    typer.echo(f"Predicting top {top_n} windows for {site_id} — not yet implemented.")


if __name__ == "__main__":
    app()
