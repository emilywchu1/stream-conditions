"""Operational CLI for stream-conditions tasks."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="stream-conditions",
    help="Stream Conditions operational CLI.",
    no_args_is_help=True,
)


@app.command()
def fetch(
    gauge_id: str = typer.Argument(..., help="USGS gauge site ID (e.g. 12345678)"),
    days: int = typer.Option(7, help="Number of days of historical data to fetch"),
) -> None:
    """Fetch hydrological and weather data for a gauge and persist to the database."""
    typer.echo(f"Fetching {days} days of data for gauge {gauge_id} …")
    # TODO: wire up sources.usgs + sources.weather + storage


@app.command("log-session")
def log_session(
    gauge_id: str = typer.Argument(..., help="USGS gauge site ID"),
    rating: int = typer.Option(..., min=1, max=5, help="Session quality 1–5"),
    notes: str = typer.Option("", help="Free-text session notes"),
) -> None:
    """Record a fishing session outcome for a gauge."""
    typer.echo(f"Logging session for gauge {gauge_id} — rating {rating}/5")
    # TODO: wire up storage


@app.command()
def predict(
    gauge_id: str = typer.Argument(..., help="USGS gauge site ID"),
    top_n: int = typer.Option(5, help="Number of upcoming windows to surface"),
) -> None:
    """Predict the next optimal fly-fishing windows for a gauge."""
    typer.echo(f"Predicting top {top_n} windows for gauge {gauge_id} …")
    # TODO: wire up features + models


if __name__ == "__main__":
    app()
