"""Integration tests for SQLiteRepository — runs against a real in-process DB."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from stream_conditions.sources.usgs import GaugeReading
from stream_conditions.sources.weather import WeatherReading
from stream_conditions.storage.database import SQLiteRepository


@pytest.fixture
def repo(tmp_path: Path) -> SQLiteRepository:
    return SQLiteRepository(tmp_path / "test.db")


def _reading(day: int = 1, discharge: float = 550.0) -> GaugeReading:
    return GaugeReading(
        site_no="12345678",
        datetime_utc=datetime(2024, 6, day, 12, 0, tzinfo=timezone.utc),
        discharge_cfs=discharge,
        stage_ft=3.2,
        water_temp_c=9.5,
    )


# ── gauge_readings ─────────────────────────────────────────────────────────────

def test_save_and_retrieve(repo: SQLiteRepository) -> None:
    repo.save_gauge_readings([_reading()])
    rows = repo.get_gauge_readings("12345678")
    assert len(rows) == 1
    assert rows[0].discharge_cfs == pytest.approx(550.0)


def test_save_is_idempotent(repo: SQLiteRepository) -> None:
    r = _reading()
    repo.save_gauge_readings([r])
    repo.save_gauge_readings([r])
    assert len(repo.get_gauge_readings("12345678")) == 1


def test_returns_count_of_attempted_rows(repo: SQLiteRepository) -> None:
    n = repo.save_gauge_readings([_reading(1), _reading(2)])
    assert n == 2


def test_date_filter_since(repo: SQLiteRepository) -> None:
    readings = [_reading(d) for d in range(1, 8)]
    repo.save_gauge_readings(readings)

    since = datetime(2024, 6, 5, tzinfo=timezone.utc)
    result = repo.get_gauge_readings("12345678", since=since)
    assert all(r.datetime_utc >= since for r in result)
    assert len(result) == 3


def test_date_filter_until(repo: SQLiteRepository) -> None:
    readings = [_reading(d) for d in range(1, 8)]
    repo.save_gauge_readings(readings)

    until = datetime(2024, 6, 3, tzinfo=timezone.utc)
    result = repo.get_gauge_readings("12345678", until=until)
    assert all(r.datetime_utc <= until for r in result)


def test_site_isolation(repo: SQLiteRepository) -> None:
    r_a = GaugeReading("AAA", datetime(2024, 1, 1, tzinfo=timezone.utc), 100.0, 1.0, 5.0)
    r_b = GaugeReading("BBB", datetime(2024, 1, 1, tzinfo=timezone.utc), 200.0, 2.0, 6.0)
    repo.save_gauge_readings([r_a, r_b])
    assert len(repo.get_gauge_readings("AAA")) == 1
    assert len(repo.get_gauge_readings("BBB")) == 1


# ── fishing_sessions ───────────────────────────────────────────────────────────

def test_log_session_returns_positive_id(repo: SQLiteRepository) -> None:
    row_id = repo.log_session(
        site_no="12345678",
        session_date=datetime(2024, 6, 15, tzinfo=timezone.utc),
        rating=4,
        notes="Great evening hatch",
    )
    assert row_id >= 1


def test_log_session_increments_ids(repo: SQLiteRepository) -> None:
    date = datetime(2024, 6, 15, tzinfo=timezone.utc)
    id1 = repo.log_session("12345678", date, 3, "")
    id2 = repo.log_session("12345678", date, 5, "")
    assert id2 > id1
