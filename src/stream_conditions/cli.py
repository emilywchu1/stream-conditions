"""Operational CLI for stream-conditions tasks."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import typer

from stream_conditions.ingest import DEFAULT_DB, backfill_gauge, fetch_all
from stream_conditions.sources.gauge_registry import list_gauges, register_gauge
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

@app.command("log-session")
def log_session(
    site_id: str = typer.Argument(..., help="USGS gauge site ID"),
    notes: str = typer.Option("", help="Free-text session notes"),
    db: Path | None = _DB_OPTION,
) -> None:
    """Record a fishing session outcome for a gauge."""
    typer.echo(f"Logging session for gauge {site_id} — not yet implemented.")


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
