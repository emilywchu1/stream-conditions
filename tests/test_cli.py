"""Tests for the log-session CLI command."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stream_conditions.cli import app
from stream_conditions.sources.gauge_registry import Gauge
from stream_conditions.storage.base import Snapshot
from stream_conditions.storage.sqlite import Database

runner = CliRunner()
UTC = timezone.utc

_GAUGE = Gauge(
    site_id="02334430",
    name="Chattahoochee River At Atlanta",
    latitude=34.15,
    longitude=-84.07,
    state_cd="GA",
    huc_cd="03130001",
    drain_area_sqmi=1040.0,
    notes="",
)

# Responses to every prompt in order:
#   gauge #, start, end, hatch_order, hatch_stage, intensity,
#   fish_count, species, notes line, empty line (end notes)
_VALID_INPUT = "\n".join([
    "1",                   # gauge → #1
    "08:00",               # start time
    "10:00",               # end time
    "1",                   # hatch order → ephemeroptera
    "1",                   # hatch stage → emerger
    "2",                   # intensity → moderate
    "5",                   # fish count
    "rainbow trout",       # species
    "Great morning hatch", # notes line
    "",                    # end notes
]) + "\n"


def _run_setup(db_path: Path, gauges: list[Gauge] = (), snapshots: list[Snapshot] = ()) -> None:
    async def _go() -> None:
        async with Database(db_path) as db:
            for g in gauges:
                await db.gauges.upsert(g)
            for s in snapshots:
                await db.snapshots.insert(s)
    asyncio.run(_go())


# ── Happy path ─────────────────────────────────────────────────────────────────

def test_log_session_happy_path(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"
    _run_setup(db_path, gauges=[_GAUGE])

    result = runner.invoke(
        app, ["log-session", "--db", str(db_path)], input=_VALID_INPUT
    )

    assert result.exit_code == 0, result.output
    assert "Session #1 logged for 02334430" in result.output

    async def _check():
        async with Database(db_path) as db:
            return await db.sessions.list_for_site("02334430")

    sessions = asyncio.run(_check())
    assert len(sessions) == 1
    s = sessions[0]
    assert s.site_id == "02334430"
    assert s.hatch_order == "ephemeroptera"
    assert s.hatch_stage == "emerger"
    assert s.hatch_intensity == 2
    assert s.fish_count == 5
    assert s.fish_species == "rainbow trout"
    assert s.notes == "Great morning hatch"
    assert s.started_at.tzinfo is not None
    assert s.ended_at is not None
    assert s.ended_at.tzinfo is not None


# ── No overlapping snapshots ───────────────────────────────────────────────────

def test_log_session_no_snapshots(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"
    _run_setup(db_path, gauges=[_GAUGE])  # no snapshots inserted

    result = runner.invoke(
        app, ["log-session", "--db", str(db_path)], input=_VALID_INPUT
    )

    assert result.exit_code == 0, result.output
    assert "Session #1 logged" in result.output
    assert "No snapshots found for this time window." in result.output


def test_log_session_shows_conditions_table_when_snapshots_exist(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"

    # Build snapshots at UTC times that fall inside the session window.
    # We don't know the local-to-UTC offset at test runtime, so we read
    # the CLI's parsed start/end back from the DB and verify against those.
    _run_setup(db_path, gauges=[_GAUGE])

    result = runner.invoke(
        app, ["log-session", "--db", str(db_path)], input=_VALID_INPUT
    )

    # Session logged successfully; no snapshot table (no snapshots seeded).
    assert result.exit_code == 0, result.output
    assert "Session #1 logged" in result.output

    # Now seed a snapshot that exactly matches the stored started_at timestamp.
    async def _seed_and_query():
        async with Database(db_path) as db:
            sessions = await db.sessions.list_for_site("02334430")
            started = sessions[0].started_at
            ended = sessions[0].ended_at
            assert ended is not None
            midpoint = started + (ended - started) / 2
            snap = Snapshot(
                site_id="02334430",
                fetched_at=midpoint,
                discharge_cfs=480.0,
                gauge_height_ft=3.2,
                water_temp_c=12.5,
                air_temp_c=18.0,
                humidity_pct=60.0,
                pressure_hpa=1012.0,
                cloud_cover_pct=10.0,
                precip_mm=0.0,
                wind_speed_kmh=8.0,
                wind_dir_deg=225.0,
            )
            await db.gauges.upsert(_GAUGE)  # ensure FK satisfied
            await db.snapshots.insert(snap)

    asyncio.run(_seed_and_query())

    # Re-run log-session with a second session; this time we get the conditions table.
    result2 = runner.invoke(
        app, ["log-session", "--db", str(db_path)], input=_VALID_INPUT
    )
    assert result2.exit_code == 0, result2.output
    assert "Conditions during session" in result2.output
    assert "480.0" in result2.output


# ── Invalid hatch order ────────────────────────────────────────────────────────

def test_log_session_invalid_hatch_order(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"
    _run_setup(db_path, gauges=[_GAUGE])

    # 8 is out of range; valid choices are 1–7
    bad_input = "1\n08:00\n10:00\n8\n"
    result = runner.invoke(
        app, ["log-session", "--db", str(db_path)], input=bad_input
    )

    assert result.exit_code != 0
    assert "Invalid" in result.output


def test_log_session_invalid_hatch_order_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"
    _run_setup(db_path, gauges=[_GAUGE])

    bad_input = "1\n08:00\n10:00\n0\n"
    result = runner.invoke(
        app, ["log-session", "--db", str(db_path)], input=bad_input
    )

    assert result.exit_code != 0
    assert "Invalid" in result.output


# ── No gauges registered ───────────────────────────────────────────────────────

def test_log_session_no_gauges(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"
    _run_setup(db_path)  # empty DB

    result = runner.invoke(
        app, ["log-session", "--db", str(db_path)], input=""
    )

    assert result.exit_code != 0
    assert "No gauges registered" in result.output


# ── Invalid gauge selection ────────────────────────────────────────────────────

def test_log_session_invalid_gauge_selection(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"
    _run_setup(db_path, gauges=[_GAUGE])  # only 1 gauge

    bad_input = "5\n"  # no gauge #5
    result = runner.invoke(
        app, ["log-session", "--db", str(db_path)], input=bad_input
    )

    assert result.exit_code != 0
    assert "Invalid" in result.output
