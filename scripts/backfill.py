#!/usr/bin/env python
"""Historical backfill — pulls N days of USGS + weather data for a gauge.

Usage:
    python scripts/backfill.py --site 02334430 --days 30
    python scripts/backfill.py --site 01646500 --days 90 --db data/sc.db

The gauge must already be registered in the database.
Runs are idempotent — timestamps already present are skipped.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Allow running from repo root without `poetry run`.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import typer
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

app = typer.Typer(add_completion=False)


@app.command()
def main(
    site: str = typer.Option(..., "--site", "-s", help="USGS site ID (e.g. 02334430)"),
    days: int = typer.Option(30, "--days", "-d", help="Number of days to backfill"),
    db: Path = typer.Option(
        Path("data/stream_conditions.db"),
        "--db",
        help="Path to SQLite database",
        envvar="DB_PATH",
    ),
) -> None:
    """Pull historical USGS + weather data and write snapshots to the database."""
    from stream_conditions.ingest import backfill_gauge

    try:
        written = asyncio.run(backfill_gauge(site, days, db))
        typer.echo(f"Backfill complete: {written} new snapshots written for {site}.")
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"Backfill failed: {exc}", err=True)
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
