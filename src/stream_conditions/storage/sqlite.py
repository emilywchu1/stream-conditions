"""SQLite backend using aiosqlite.

Usage:
    async with Database() as db:          # :memory: default
    async with Database("data/sc.db") as db:
        gauge = await db.gauges.get("02334430")
        await db.snapshots.insert(snap)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from stream_conditions.sources.gauge_registry import Gauge
from stream_conditions.storage.base import Prediction, Session, Snapshot

logger = logging.getLogger(__name__)

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _row_to_gauge(row: aiosqlite.Row) -> Gauge:
    return Gauge(
        site_id=row["site_id"],
        name=row["name"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        state_cd=row["state"],
        huc_cd=row["huc_cd"],
        drain_area_sqmi=row["drain_area_sqmi"],
        notes=row["notes"],
    )


def _row_to_snapshot(row: aiosqlite.Row) -> Snapshot:
    return Snapshot(
        id=row["id"],
        site_id=row["site_id"],
        fetched_at=_dt(row["fetched_at"]),  # type: ignore[arg-type]
        discharge_cfs=row["discharge_cfs"],
        gauge_height_ft=row["gauge_height_ft"],
        water_temp_c=row["water_temp_c"],
        air_temp_c=row["air_temp_c"],
        humidity_pct=row["humidity_pct"],
        pressure_hpa=row["pressure_hpa"],
        cloud_cover_pct=row["cloud_cover_pct"],
        precip_mm=row["precip_mm"],
        wind_speed_kmh=row["wind_speed_kmh"],
        wind_dir_deg=row["wind_dir_deg"],
    )


def _row_to_session(row: aiosqlite.Row) -> Session:
    return Session(
        id=row["id"],
        site_id=row["site_id"],
        started_at=_dt(row["started_at"]),  # type: ignore[arg-type]
        ended_at=_dt(row["ended_at"]),
        hatch_order=row["hatch_order"],
        hatch_stage=row["hatch_stage"],
        hatch_intensity=row["hatch_intensity"],
        fish_count=row["fish_count"],
        fish_species=row["fish_species"],
        notes=row["notes"],
    )


def _row_to_prediction(row: aiosqlite.Row) -> Prediction:
    return Prediction(
        id=row["id"],
        site_id=row["site_id"],
        generated_at=_dt(row["generated_at"]),  # type: ignore[arg-type]
        target_window_start=_dt(row["target_window_start"]),  # type: ignore[arg-type]
        target_window_end=_dt(row["target_window_end"]),  # type: ignore[arg-type]
        score=row["score"],
        model_version=row["model_version"],
        features_json=row["features_json"],
        actual_outcome=row["actual_outcome"],
    )


# ── Repo implementations ───────────────────────────────────────────────────────

class GaugeSQLiteRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._c = conn

    async def upsert(self, gauge: Gauge) -> None:
        await self._c.execute(
            """INSERT INTO gauges
                   (site_id, name, latitude, longitude, state, huc_cd,
                    drain_area_sqmi, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(site_id) DO UPDATE SET
                   name            = excluded.name,
                   latitude        = excluded.latitude,
                   longitude       = excluded.longitude,
                   state           = excluded.state,
                   huc_cd          = excluded.huc_cd,
                   drain_area_sqmi = excluded.drain_area_sqmi,
                   notes           = excluded.notes""",
            (
                gauge.site_id, gauge.name, gauge.latitude, gauge.longitude,
                gauge.state_cd, gauge.huc_cd, gauge.drain_area_sqmi, gauge.notes,
            ),
        )
        await self._c.commit()

    async def get(self, site_id: str) -> Gauge | None:
        async with self._c.execute(
            "SELECT * FROM gauges WHERE site_id = ?", (site_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_gauge(row) if row else None

    async def list(self) -> list[Gauge]:
        async with self._c.execute("SELECT * FROM gauges ORDER BY name") as cur:
            rows = await cur.fetchall()
        return [_row_to_gauge(r) for r in rows]

    async def delete(self, site_id: str) -> None:
        await self._c.execute("DELETE FROM gauges WHERE site_id = ?", (site_id,))
        await self._c.commit()


class SnapshotSQLiteRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._c = conn

    async def insert(self, snapshot: Snapshot) -> int:
        cur = await self._c.execute(
            """INSERT OR IGNORE INTO snapshots
                   (site_id, fetched_at, discharge_cfs, gauge_height_ft,
                    water_temp_c, air_temp_c, humidity_pct, pressure_hpa,
                    cloud_cover_pct, precip_mm, wind_speed_kmh, wind_dir_deg)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.site_id, _iso(snapshot.fetched_at),
                snapshot.discharge_cfs, snapshot.gauge_height_ft,
                snapshot.water_temp_c, snapshot.air_temp_c,
                snapshot.humidity_pct, snapshot.pressure_hpa,
                snapshot.cloud_cover_pct, snapshot.precip_mm,
                snapshot.wind_speed_kmh, snapshot.wind_dir_deg,
            ),
        )
        await self._c.commit()
        # rowcount == 0 means the unique index rejected a duplicate — caller gets 0.
        return (cur.lastrowid or 0) if cur.rowcount else 0

    async def get_range(
        self, site_id: str, since: datetime, until: datetime
    ) -> list[Snapshot]:
        async with self._c.execute(
            """SELECT * FROM snapshots
               WHERE site_id = ? AND fetched_at >= ? AND fetched_at <= ?
               ORDER BY fetched_at""",
            (site_id, _iso(since), _iso(until)),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_snapshot(r) for r in rows]

    async def latest(self, site_id: str) -> Snapshot | None:
        async with self._c.execute(
            """SELECT * FROM snapshots WHERE site_id = ?
               ORDER BY fetched_at DESC LIMIT 1""",
            (site_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_snapshot(row) if row else None


class SessionSQLiteRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._c = conn

    async def insert(self, session: Session) -> int:
        cur = await self._c.execute(
            """INSERT INTO sessions
                   (site_id, started_at, ended_at, hatch_order, hatch_stage,
                    hatch_intensity, fish_count, fish_species, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.site_id, _iso(session.started_at), _iso(session.ended_at),
                session.hatch_order, session.hatch_stage, session.hatch_intensity,
                session.fish_count, session.fish_species, session.notes,
            ),
        )
        await self._c.commit()
        return cur.lastrowid or 0

    async def get(self, session_id: int) -> Session | None:
        async with self._c.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_session(row) if row else None

    async def list_for_site(
        self, site_id: str, since: datetime | None = None
    ) -> list[Session]:
        if since:
            sql = ("SELECT * FROM sessions WHERE site_id = ? AND started_at >= ?"
                   " ORDER BY started_at")
            args: tuple[Any, ...] = (site_id, _iso(since))
        else:
            sql = "SELECT * FROM sessions WHERE site_id = ? ORDER BY started_at"
            args = (site_id,)
        async with self._c.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [_row_to_session(r) for r in rows]

    async def update(self, session: Session) -> None:
        if session.id is None:
            raise ValueError("Cannot update a session without an id")
        await self._c.execute(
            """UPDATE sessions SET
                   started_at      = ?,
                   ended_at        = ?,
                   hatch_order     = ?,
                   hatch_stage     = ?,
                   hatch_intensity = ?,
                   fish_count      = ?,
                   fish_species    = ?,
                   notes           = ?
               WHERE id = ?""",
            (
                _iso(session.started_at), _iso(session.ended_at),
                session.hatch_order, session.hatch_stage, session.hatch_intensity,
                session.fish_count, session.fish_species, session.notes,
                session.id,
            ),
        )
        await self._c.commit()

    async def delete(self, session_id: int) -> None:
        await self._c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self._c.commit()


class PredictionSQLiteRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._c = conn

    async def insert(self, prediction: Prediction) -> int:
        cur = await self._c.execute(
            """INSERT INTO predictions
                   (site_id, generated_at, target_window_start, target_window_end,
                    score, model_version, features_json, actual_outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                prediction.site_id, _iso(prediction.generated_at),
                _iso(prediction.target_window_start), _iso(prediction.target_window_end),
                prediction.score, prediction.model_version,
                prediction.features_json, prediction.actual_outcome,
            ),
        )
        await self._c.commit()
        return cur.lastrowid or 0

    async def get(self, prediction_id: int) -> Prediction | None:
        async with self._c.execute(
            "SELECT * FROM predictions WHERE id = ?", (prediction_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_prediction(row) if row else None

    async def list_for_site(self, site_id: str) -> list[Prediction]:
        async with self._c.execute(
            "SELECT * FROM predictions WHERE site_id = ? ORDER BY generated_at",
            (site_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_prediction(r) for r in rows]

    async def backfill_outcome(self, prediction_id: int, actual_outcome: int) -> None:
        await self._c.execute(
            "UPDATE predictions SET actual_outcome = ? WHERE id = ?",
            (actual_outcome, prediction_id),
        )
        await self._c.commit()


# ── Database ───────────────────────────────────────────────────────────────────

class Database:
    """Async SQLite database. Use as an async context manager.

    Examples
    --------
    async with Database() as db:               # in-memory
    async with Database("data/sc.db") as db:   # persistent
        await db.gauges.upsert(gauge)
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "Database":
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA)
        logger.debug("Database connected: %s", self._path)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def _c(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "Database not connected — use `async with Database() as db:`"
            )
        return self._conn

    @property
    def gauges(self) -> GaugeSQLiteRepo:
        return GaugeSQLiteRepo(self._c)

    @property
    def snapshots(self) -> SnapshotSQLiteRepo:
        return SnapshotSQLiteRepo(self._c)

    @property
    def sessions(self) -> SessionSQLiteRepo:
        return SessionSQLiteRepo(self._c)

    @property
    def predictions(self) -> PredictionSQLiteRepo:
        return PredictionSQLiteRepo(self._c)
