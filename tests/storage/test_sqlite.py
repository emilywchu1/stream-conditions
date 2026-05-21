"""Tests for the SQLite storage layer.

In-memory Database for unit tests; tmp_path for integration persistence test.
Concurrent-write tests use asyncio.gather to verify aiosqlite serialises correctly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stream_conditions.sources.gauge_registry import Gauge
from stream_conditions.storage.base import (
    GaugeRepo,
    Prediction,
    PredictionRepo,
    Session,
    SessionRepo,
    Snapshot,
    SnapshotRepo,
)
from stream_conditions.storage.sqlite import Database

UTC = timezone.utc


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
async def db() -> Database:  # type: ignore[misc]
    async with Database(":memory:") as database:
        yield database


def _gauge(site_id: str = "02334430") -> Gauge:
    return Gauge(
        site_id=site_id,
        name="Test Gauge",
        latitude=34.15,
        longitude=-84.07,
        state_cd="13",
        huc_cd="03130001",
        drain_area_sqmi=1040.0,
        notes="",
    )


def _snap(
    site_id: str = "02334430",
    hour: int = 0,
    discharge: float = 500.0,
) -> Snapshot:
    return Snapshot(
        site_id=site_id,
        fetched_at=datetime(2024, 4, 1, hour, 0, tzinfo=UTC),
        discharge_cfs=discharge,
        gauge_height_ft=3.5,
        water_temp_c=10.0,
        air_temp_c=15.0,
        humidity_pct=70.0,
        pressure_hpa=1010.0,
        cloud_cover_pct=30.0,
        precip_mm=0.0,
        wind_speed_kmh=12.0,
        wind_dir_deg=270.0,
    )


def _session(site_id: str = "02334430") -> Session:
    return Session(
        site_id=site_id,
        started_at=datetime(2024, 4, 1, 18, 0, tzinfo=UTC),
        ended_at=datetime(2024, 4, 1, 21, 0, tzinfo=UTC),
        hatch_order="ephemeroptera",
        hatch_stage="emerger",
        hatch_intensity=2,
        fish_count=4,
        fish_species="brown trout",
        notes="Great evening hatch",
    )


def _prediction(site_id: str = "02334430") -> Prediction:
    return Prediction(
        site_id=site_id,
        generated_at=datetime(2024, 4, 1, 8, 0, tzinfo=UTC),
        target_window_start=datetime(2024, 4, 1, 17, 0, tzinfo=UTC),
        target_window_end=datetime(2024, 4, 1, 21, 0, tzinfo=UTC),
        score=0.82,
        model_version="v1.0",
        features_json='{"discharge": 500, "temp": 10}',
    )


# ── Protocol conformance ───────────────────────────────────────────────────────

async def test_repos_satisfy_protocols(db: Database) -> None:
    assert isinstance(db.gauges, GaugeRepo)
    assert isinstance(db.snapshots, SnapshotRepo)
    assert isinstance(db.sessions, SessionRepo)
    assert isinstance(db.predictions, PredictionRepo)


# ── GaugeRepo ──────────────────────────────────────────────────────────────────

async def test_gauge_upsert_and_get(db: Database) -> None:
    g = _gauge()
    await db.gauges.upsert(g)
    result = await db.gauges.get(g.site_id)
    assert result is not None
    assert result.site_id == g.site_id
    assert result.latitude == pytest.approx(g.latitude)
    assert result.drain_area_sqmi == pytest.approx(1040.0)


async def test_gauge_get_missing_returns_none(db: Database) -> None:
    assert await db.gauges.get("99999999") is None


async def test_gauge_upsert_updates_existing(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    updated = _gauge()
    updated.notes = "updated note"
    await db.gauges.upsert(updated)
    result = await db.gauges.get("02334430")
    assert result is not None
    assert result.notes == "updated note"


async def test_gauge_list(db: Database) -> None:
    await db.gauges.upsert(_gauge("02334430"))
    await db.gauges.upsert(_gauge("01646500"))
    gauges = await db.gauges.list()
    assert len(gauges) == 2


async def test_gauge_delete(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    await db.gauges.delete("02334430")
    assert await db.gauges.get("02334430") is None


async def test_gauge_delete_cascades_snapshots(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    await db.snapshots.insert(_snap())
    await db.gauges.delete("02334430")
    results = await db.snapshots.get_range(
        "02334430",
        since=datetime(2024, 1, 1, tzinfo=UTC),
        until=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert results == []


# ── SnapshotRepo ───────────────────────────────────────────────────────────────

async def test_snapshot_insert_returns_id(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    row_id = await db.snapshots.insert(_snap())
    assert row_id >= 1


async def test_snapshot_get_range(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    for hour in range(6):
        await db.snapshots.insert(_snap(hour=hour))

    results = await db.snapshots.get_range(
        "02334430",
        since=datetime(2024, 4, 1, 2, 0, tzinfo=UTC),
        until=datetime(2024, 4, 1, 4, 0, tzinfo=UTC),
    )
    assert len(results) == 3  # hours 2, 3, 4 inclusive


async def test_snapshot_get_range_sorted(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    for hour in [5, 1, 3]:
        await db.snapshots.insert(_snap(hour=hour))
    results = await db.snapshots.get_range(
        "02334430",
        since=datetime(2024, 4, 1, tzinfo=UTC),
        until=datetime(2024, 4, 2, tzinfo=UTC),
    )
    timestamps = [r.fetched_at for r in results]
    assert timestamps == sorted(timestamps)


async def test_snapshot_latest(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    for hour in [1, 5, 3]:
        await db.snapshots.insert(_snap(hour=hour, discharge=float(hour * 100)))
    latest = await db.snapshots.latest("02334430")
    assert latest is not None
    assert latest.discharge_cfs == pytest.approx(500.0)  # hour 5


async def test_snapshot_latest_empty_returns_none(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    assert await db.snapshots.latest("02334430") is None


async def test_snapshot_roundtrip_preserves_all_fields(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    snap = _snap()
    await db.snapshots.insert(snap)
    results = await db.snapshots.get_range(
        "02334430",
        since=datetime(2024, 4, 1, tzinfo=UTC),
        until=datetime(2024, 4, 2, tzinfo=UTC),
    )
    r = results[0]
    assert r.discharge_cfs == pytest.approx(500.0)
    assert r.gauge_height_ft == pytest.approx(3.5)
    assert r.water_temp_c == pytest.approx(10.0)
    assert r.wind_speed_kmh == pytest.approx(12.0)
    assert r.wind_dir_deg == pytest.approx(270.0)
    assert r.fetched_at.tzinfo is not None


async def test_snapshot_site_isolation(db: Database) -> None:
    await db.gauges.upsert(_gauge("02334430"))
    await db.gauges.upsert(_gauge("01646500"))
    await db.snapshots.insert(_snap("02334430"))
    await db.snapshots.insert(_snap("01646500"))
    results = await db.snapshots.get_range(
        "02334430",
        since=datetime(2024, 1, 1, tzinfo=UTC),
        until=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert all(r.site_id == "02334430" for r in results)


async def test_concurrent_snapshot_inserts(db: Database) -> None:
    await db.gauges.upsert(_gauge())

    async def _insert(hour: int) -> None:
        await db.snapshots.insert(_snap(hour=hour, discharge=float(hour * 10)))

    await asyncio.gather(*[_insert(h) for h in range(20)])

    results = await db.snapshots.get_range(
        "02334430",
        since=datetime(2024, 4, 1, 0, 0, tzinfo=UTC),
        until=datetime(2024, 4, 1, 23, 59, tzinfo=UTC),
    )
    assert len(results) == 20


# ── SessionRepo ────────────────────────────────────────────────────────────────

async def test_session_insert_and_get(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    row_id = await db.sessions.insert(_session())
    result = await db.sessions.get(row_id)
    assert result is not None
    assert result.id == row_id
    assert result.hatch_order == "ephemeroptera"
    assert result.hatch_intensity == 2
    assert result.fish_count == 4


async def test_session_get_missing_returns_none(db: Database) -> None:
    assert await db.sessions.get(999) is None


async def test_session_list_for_site(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    await db.sessions.insert(_session())
    await db.sessions.insert(_session())
    results = await db.sessions.list_for_site("02334430")
    assert len(results) == 2


async def test_session_list_since_filter(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    early = Session(
        site_id="02334430",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        notes="early",
    )
    late = Session(
        site_id="02334430",
        started_at=datetime(2024, 6, 1, tzinfo=UTC),
        notes="late",
    )
    await db.sessions.insert(early)
    await db.sessions.insert(late)
    results = await db.sessions.list_for_site(
        "02334430", since=datetime(2024, 4, 1, tzinfo=UTC)
    )
    assert len(results) == 1
    assert results[0].notes == "late"


async def test_session_update(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    row_id = await db.sessions.insert(_session())
    sess = await db.sessions.get(row_id)
    assert sess is not None
    sess.fish_count = 12
    sess.notes = "updated"
    await db.sessions.update(sess)
    updated = await db.sessions.get(row_id)
    assert updated is not None
    assert updated.fish_count == 12
    assert updated.notes == "updated"


async def test_session_update_without_id_raises(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    sess = _session()
    with pytest.raises(ValueError, match="id"):
        await db.sessions.update(sess)


async def test_session_delete(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    row_id = await db.sessions.insert(_session())
    await db.sessions.delete(row_id)
    assert await db.sessions.get(row_id) is None


async def test_session_timestamps_are_utc(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    row_id = await db.sessions.insert(_session())
    result = await db.sessions.get(row_id)
    assert result is not None
    assert result.started_at.tzinfo is not None
    assert result.ended_at is not None
    assert result.ended_at.tzinfo is not None


async def test_concurrent_session_inserts(db: Database) -> None:
    await db.gauges.upsert(_gauge())

    async def _insert(i: int) -> None:
        sess = Session(
            site_id="02334430",
            started_at=datetime(2024, 4, i + 1, tzinfo=UTC),
        )
        await db.sessions.insert(sess)

    await asyncio.gather(*[_insert(i) for i in range(10)])
    results = await db.sessions.list_for_site("02334430")
    assert len(results) == 10


# ── PredictionRepo ─────────────────────────────────────────────────────────────

async def test_prediction_insert_and_get(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    row_id = await db.predictions.insert(_prediction())
    result = await db.predictions.get(row_id)
    assert result is not None
    assert result.id == row_id
    assert result.score == pytest.approx(0.82)
    assert result.model_version == "v1.0"
    assert result.actual_outcome is None


async def test_prediction_get_missing_returns_none(db: Database) -> None:
    assert await db.predictions.get(999) is None


async def test_prediction_list_for_site(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    await db.predictions.insert(_prediction())
    await db.predictions.insert(_prediction())
    results = await db.predictions.list_for_site("02334430")
    assert len(results) == 2


async def test_prediction_backfill_outcome(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    row_id = await db.predictions.insert(_prediction())
    await db.predictions.backfill_outcome(row_id, actual_outcome=1)
    result = await db.predictions.get(row_id)
    assert result is not None
    assert result.actual_outcome == 1


async def test_prediction_features_json_preserved(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    pred = _prediction()
    pred.features_json = '{"discharge": 500, "temp": 10.5, "wind": 12}'
    row_id = await db.predictions.insert(pred)
    result = await db.predictions.get(row_id)
    assert result is not None
    assert result.features_json == pred.features_json


async def test_prediction_timestamps_are_utc(db: Database) -> None:
    await db.gauges.upsert(_gauge())
    row_id = await db.predictions.insert(_prediction())
    result = await db.predictions.get(row_id)
    assert result is not None
    assert result.generated_at.tzinfo is not None
    assert result.target_window_start.tzinfo is not None
    assert result.target_window_end.tzinfo is not None


# ── Integration: file-based persistence ───────────────────────────────────────

async def test_file_database_persists_across_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"
    g = _gauge()

    async with Database(db_path) as db1:
        await db1.gauges.upsert(g)

    async with Database(db_path) as db2:
        result = await db2.gauges.get(g.site_id)

    assert result is not None
    assert result.site_id == g.site_id
    assert result.latitude == pytest.approx(g.latitude)


async def test_file_database_snapshots_persist(tmp_path: Path) -> None:
    db_path = tmp_path / "sc.db"

    async with Database(db_path) as db1:
        await db1.gauges.upsert(_gauge())
        for h in range(5):
            await db1.snapshots.insert(_snap(hour=h))

    async with Database(db_path) as db2:
        results = await db2.snapshots.get_range(
            "02334430",
            since=datetime(2024, 4, 1, tzinfo=UTC),
            until=datetime(2024, 4, 2, tzinfo=UTC),
        )

    assert len(results) == 5
