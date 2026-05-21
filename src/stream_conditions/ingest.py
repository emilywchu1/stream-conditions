"""Core fetch and backfill logic.

Both scripts/ and cli.py call these async functions directly so the implementation
lives in one place and is straightforwardly testable.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from stream_conditions.sources.gauge_registry import Gauge
from stream_conditions.sources.usgs import USGSClient, USGSReading
from stream_conditions.sources.weather import HourlyForecast, WeatherClient, WeatherReading
from stream_conditions.storage.base import Snapshot
from stream_conditions.storage.sqlite import Database

logger = logging.getLogger(__name__)

UTC = timezone.utc
DEFAULT_DB = Path("data/stream_conditions.db")

# Freshness window: skip fetch if the latest snapshot is less than this old.
# At 15-minute scheduler cadence, 10 min prevents double-fetches without
# blocking the next legitimate run.
_FRESH_THRESHOLD_S: int = 600


# ── Sync helpers (run in thread pool via asyncio.to_thread) ───────────────────

def _usgs_current(site_id: str) -> USGSReading:
    with USGSClient() as c:
        return c.get_current(site_id)


def _usgs_historical(site_id: str, start: date, end: date) -> list[USGSReading]:
    with USGSClient() as c:
        return c.get_historical(site_id, start, end)


def _wx_current(lat: float, lon: float) -> WeatherReading:
    with WeatherClient() as c:
        return c.get_current(lat, lon)


def _wx_historical(
    lat: float, lon: float, start: date, end: date
) -> list[HourlyForecast]:
    with WeatherClient() as c:
        return c.get_historical(lat, lon, start, end)


# ── Snapshot assembly ─────────────────────────────────────────────────────────

def _join_current(gauge: Gauge, usgs: USGSReading, wx: WeatherReading) -> Snapshot:
    return Snapshot(
        site_id=gauge.site_id,
        fetched_at=usgs.timestamp,
        discharge_cfs=usgs.discharge_cfs,
        gauge_height_ft=usgs.gauge_height_ft,
        water_temp_c=usgs.water_temp_c,
        air_temp_c=wx.air_temp_c,
        humidity_pct=wx.humidity_pct,
        pressure_hpa=wx.pressure_hpa,
        cloud_cover_pct=wx.cloud_cover_pct,
        precip_mm=wx.precipitation_mm,
        wind_speed_kmh=wx.wind_speed_kmh,
        wind_dir_deg=wx.wind_direction_deg,
    )


def _join_historical(
    site_id: str,
    usgs_readings: list[USGSReading],
    wx_forecasts: list[HourlyForecast],
) -> list[Snapshot]:
    """Align USGS 15-min readings with hourly weather by truncating to the hour."""
    wx_by_hour: dict[datetime, HourlyForecast] = {
        f.timestamp.replace(minute=0, second=0, microsecond=0): f
        for f in wx_forecasts
    }

    snaps: list[Snapshot] = []
    for r in usgs_readings:
        wx = wx_by_hour.get(r.timestamp.replace(minute=0, second=0, microsecond=0))
        snaps.append(
            Snapshot(
                site_id=site_id,
                fetched_at=r.timestamp,
                discharge_cfs=r.discharge_cfs,
                gauge_height_ft=r.gauge_height_ft,
                water_temp_c=r.water_temp_c,
                air_temp_c=wx.air_temp_c if wx else None,
                humidity_pct=wx.humidity_pct if wx else None,
                pressure_hpa=wx.pressure_hpa if wx else None,
                cloud_cover_pct=wx.cloud_cover_pct if wx else None,
                precip_mm=wx.precipitation_mm if wx else None,
                wind_speed_kmh=wx.wind_speed_kmh if wx else None,
                wind_dir_deg=wx.wind_direction_deg if wx else None,
            )
        )
    return snaps


# ── Per-gauge fetch ───────────────────────────────────────────────────────────

async def _fetch_one(gauge: Gauge, db: Database, now: datetime) -> bool:
    """Fetch and persist the current snapshot for one gauge.

    Returns True on success or skip, False on any fetch/write error.
    """
    # Idempotency: skip if latest snapshot is still fresh.
    latest = await db.snapshots.latest(gauge.site_id)
    if latest is not None:
        age_s = (now - latest.fetched_at).total_seconds()
        if age_s < _FRESH_THRESHOLD_S:
            logger.info(
                "[%s] snapshot is %.0fs old — skipping", gauge.site_id, age_s
            )
            return True

    try:
        usgs_r, wx_r = await asyncio.gather(
            asyncio.to_thread(_usgs_current, gauge.site_id),
            asyncio.to_thread(_wx_current, gauge.latitude, gauge.longitude),
        )
    except Exception as exc:
        logger.warning("[%s] fetch failed: %s", gauge.site_id, exc)
        return False

    snap = _join_current(gauge, usgs_r, wx_r)

    try:
        row_id = await db.snapshots.insert(snap)
    except Exception as exc:
        logger.error("[%s] DB write failed: %s", gauge.site_id, exc)
        return False

    if row_id:
        logger.info(
            "[%s] snapshot written @ %s  discharge=%.1f cfs  air=%.1f°C",
            gauge.site_id,
            snap.fetched_at.isoformat(),
            snap.discharge_cfs or 0.0,
            snap.air_temp_c or 0.0,
        )
    else:
        logger.info("[%s] snapshot already present (duplicate skipped)", gauge.site_id)

    return True


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_all(db_path: Path = DEFAULT_DB) -> dict[str, bool]:
    """Fetch current conditions for every registered gauge concurrently.

    Returns a mapping of site_id → success.  Raises SystemExit(1) if *all*
    gauges fail; partial failure still returns with individual False values.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with Database(db_path) as db:
        gauges = await db.gauges.list()

    if not gauges:
        logger.warning("No registered gauges in DB — nothing to fetch.")
        return {}

    now = datetime.now(UTC)

    async with Database(db_path) as db:
        raw = await asyncio.gather(
            *[_fetch_one(g, db, now) for g in gauges],
            return_exceptions=True,
        )

    results: dict[str, bool] = {}
    for gauge, outcome in zip(gauges, raw):
        if isinstance(outcome, BaseException):
            logger.error("[%s] unhandled error: %s", gauge.site_id, outcome)
            results[gauge.site_id] = False
        else:
            results[gauge.site_id] = bool(outcome)

    ok = sum(results.values())
    total = len(results)
    logger.info("fetch_all complete: %d/%d gauges succeeded", ok, total)
    return results


async def backfill_gauge(
    site_id: str,
    days: int,
    db_path: Path = DEFAULT_DB,
) -> int:
    """Write historical snapshots for *site_id* for the past *days* days.

    Skips timestamps already present (idempotent).
    Returns the number of new snapshots written.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    end = date.today()
    start = end - timedelta(days=days)

    async with Database(db_path) as db:
        gauge = await db.gauges.get(site_id)

    if gauge is None:
        raise ValueError(
            f"Gauge {site_id!r} is not registered. "
            "Run `stream-conditions register <site_id>` first."
        )

    logger.info(
        "[%s] backfilling %d days (%s → %s)", site_id, days, start, end
    )

    # Fetch both in parallel (blocking, so run in threads).
    usgs_readings, wx_forecasts = await asyncio.gather(
        asyncio.to_thread(_usgs_historical, site_id, start, end),
        asyncio.to_thread(
            _wx_historical, gauge.latitude, gauge.longitude, start, end
        ),
    )

    logger.info(
        "[%s] fetched %d USGS readings, %d weather hours",
        site_id, len(usgs_readings), len(wx_forecasts),
    )

    snapshots = _join_historical(site_id, usgs_readings, wx_forecasts)

    written = 0
    async with Database(db_path) as db:
        for snap in snapshots:
            row_id = await db.snapshots.insert(snap)
            if row_id:
                written += 1

    logger.info(
        "[%s] backfill done: %d new / %d total USGS readings",
        site_id, written, len(snapshots),
    )
    return written
