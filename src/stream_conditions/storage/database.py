"""Storage layer — abstract Repository interface + SQLite implementation.

The abstract base makes it straightforward to swap in a Postgres backend:
implement Repository with psycopg2/asyncpg and point the factory at it.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Sequence

from stream_conditions.sources.usgs import GaugeReading
from stream_conditions.sources.weather import WeatherReading


# ── Schema ─────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS gauge_readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site_no       TEXT    NOT NULL,
    datetime_utc  TEXT    NOT NULL,
    discharge_cfs REAL,
    stage_ft      REAL,
    water_temp_c  REAL,
    UNIQUE (site_no, datetime_utc)
);

CREATE TABLE IF NOT EXISTS weather_readings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    site_no          TEXT    NOT NULL,
    datetime_utc     TEXT    NOT NULL,
    air_temp_c       REAL,
    precipitation_mm REAL,
    wind_speed_ms    REAL,
    cloud_cover_pct  REAL,
    UNIQUE (site_no, datetime_utc)
);

CREATE TABLE IF NOT EXISTS fishing_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    site_no      TEXT    NOT NULL,
    session_date TEXT    NOT NULL,
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    notes        TEXT    NOT NULL DEFAULT ''
);
"""


# ── Abstract interface ─────────────────────────────────────────────────────────

class Repository(ABC):
    """Database-agnostic interface for stream-conditions persistence."""

    @abstractmethod
    def save_gauge_readings(self, readings: Sequence[GaugeReading]) -> int:
        """Upsert gauge readings; return the number of rows attempted."""
        ...

    @abstractmethod
    def save_weather_readings(
        self, readings: Sequence[WeatherReading], site_no: str
    ) -> int:
        """Upsert weather readings for *site_no*; return rows attempted."""
        ...

    @abstractmethod
    def log_session(
        self,
        site_no: str,
        session_date: datetime,
        rating: int,
        notes: str,
    ) -> int:
        """Insert a fishing session record; return the new row ID."""
        ...

    @abstractmethod
    def get_gauge_readings(
        self,
        site_no: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[GaugeReading]:
        """Retrieve gauge readings for *site_no*, optionally bounded by time."""
        ...


# ── SQLite implementation ──────────────────────────────────────────────────────

class SQLiteRepository(Repository):
    """sqlite3-backed repository using WAL mode for concurrent read safety."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._apply_schema()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _apply_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

    def save_gauge_readings(self, readings: Sequence[GaugeReading]) -> int:
        rows = [
            (
                r.site_no,
                r.datetime_utc.isoformat(),
                r.discharge_cfs,
                r.stage_ft,
                r.water_temp_c,
            )
            for r in readings
        ]
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO gauge_readings
                       (site_no, datetime_utc, discharge_cfs, stage_ft, water_temp_c)
                   VALUES (?, ?, ?, ?, ?)""",
                rows,
            )
        return len(rows)

    def save_weather_readings(
        self, readings: Sequence[WeatherReading], site_no: str
    ) -> int:
        rows = [
            (
                site_no,
                r.datetime_utc.isoformat(),
                r.air_temp_c,
                r.precipitation_mm,
                r.wind_speed_ms,
                r.cloud_cover_pct,
            )
            for r in readings
        ]
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO weather_readings
                       (site_no, datetime_utc, air_temp_c, precipitation_mm,
                        wind_speed_ms, cloud_cover_pct)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return len(rows)

    def log_session(
        self,
        site_no: str,
        session_date: datetime,
        rating: int,
        notes: str,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO fishing_sessions (site_no, session_date, rating, notes)
                   VALUES (?, ?, ?, ?)""",
                (site_no, session_date.isoformat(), rating, notes),
            )
            return int(cur.lastrowid or 0)

    def get_gauge_readings(
        self,
        site_no: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[GaugeReading]:
        sql = "SELECT * FROM gauge_readings WHERE site_no = ?"
        args: list[object] = [site_no]
        if since:
            sql += " AND datetime_utc >= ?"
            args.append(since.isoformat())
        if until:
            sql += " AND datetime_utc <= ?"
            args.append(until.isoformat())
        sql += " ORDER BY datetime_utc"

        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()

        return [
            GaugeReading(
                site_no=row["site_no"],
                datetime_utc=datetime.fromisoformat(row["datetime_utc"]),
                discharge_cfs=row["discharge_cfs"],
                stage_ft=row["stage_ft"],
                water_temp_c=row["water_temp_c"],
            )
            for row in rows
        ]
