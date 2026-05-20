"""Unit tests for the Open-Meteo weather client."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import vcr as vcrpy

from stream_conditions.sources.weather import (
    HourlyForecast,
    WeatherClient,
    WeatherReading,
    _parse_current,
    _parse_hourly,
    _to_float,
)

CASSETTE_DIR = "tests/sources/cassettes"

_vcr = vcrpy.VCR(cassette_library_dir=CASSETTE_DIR)

LAT, LON = 34.1606, -84.0744  # Buford Dam, Chattahoochee


# ── _to_float ──────────────────────────────────────────────────────────────────

def test_to_float_numeric_string() -> None:
    assert _to_float("22.9") == pytest.approx(22.9)


def test_to_float_integer() -> None:
    assert _to_float(66) == pytest.approx(66.0)


def test_to_float_none_returns_none() -> None:
    assert _to_float(None) is None


def test_to_float_non_numeric_returns_none() -> None:
    assert _to_float("N/A") is None


# ── _parse_current ─────────────────────────────────────────────────────────────

def _current_payload() -> dict:  # type: ignore[type-arg]
    return {
        "latitude": 34.15,
        "longitude": -84.06,
        "current": {
            "time": "2026-05-19T22:00",
            "temperature_2m": 22.9,
            "relative_humidity_2m": 66,
            "surface_pressure": 983.3,
            "cloud_cover": 0,
            "precipitation": 0.0,
            "wind_speed_10m": 8.4,
            "wind_direction_10m": 110,
        },
    }


def test_parse_current_returns_reading() -> None:
    r = _parse_current(_current_payload())
    assert isinstance(r, WeatherReading)
    assert r.air_temp_c == pytest.approx(22.9)
    assert r.humidity_pct == pytest.approx(66.0)
    assert r.wind_speed_kmh == pytest.approx(8.4)
    assert r.wind_direction_deg == pytest.approx(110.0)


def test_parse_current_timestamp_is_utc() -> None:
    r = _parse_current(_current_payload())
    assert r.timestamp == datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc)
    assert r.timestamp.tzinfo == timezone.utc


def test_parse_current_coords() -> None:
    r = _parse_current(_current_payload())
    assert r.latitude == pytest.approx(34.15)
    assert r.longitude == pytest.approx(-84.06)


# ── _parse_hourly ──────────────────────────────────────────────────────────────

def _hourly_payload() -> dict:  # type: ignore[type-arg]
    return {
        "hourly": {
            "time": ["2026-05-19T00:00", "2026-05-19T01:00"],
            "temperature_2m": [20.0, 19.5],
            "relative_humidity_2m": [70, 72],
            "surface_pressure": [985.0, 984.5],
            "cloud_cover": [10, 20],
            "precipitation": [0.0, 0.1],
            "wind_speed_10m": [5.0, 6.0],
            "wind_direction_10m": [90, 100],
        }
    }


def test_parse_hourly_count() -> None:
    result = _parse_hourly(_hourly_payload())
    assert len(result) == 2


def test_parse_hourly_values() -> None:
    result = _parse_hourly(_hourly_payload())
    first = result[0]
    assert isinstance(first, HourlyForecast)
    assert first.air_temp_c == pytest.approx(20.0)
    assert first.humidity_pct == pytest.approx(70.0)
    assert first.precipitation_mm == pytest.approx(0.0)
    assert first.wind_direction_deg == pytest.approx(90.0)


def test_parse_hourly_timestamps_are_utc() -> None:
    result = _parse_hourly(_hourly_payload())
    assert result[0].timestamp == datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc)
    assert result[1].timestamp == datetime(2026, 5, 19, 1, 0, tzinfo=timezone.utc)


def test_parse_hourly_none_values() -> None:
    payload = {
        "hourly": {
            "time": ["2026-05-19T00:00"],
            "temperature_2m": [None],
            "relative_humidity_2m": [],
            "surface_pressure": [],
            "cloud_cover": [],
            "precipitation": [],
            "wind_speed_10m": [],
            "wind_direction_10m": [],
        }
    }
    result = _parse_hourly(payload)
    assert result[0].air_temp_c is None
    assert result[0].humidity_pct is None


# ── Integration tests via vcrpy ────────────────────────────────────────────────

@_vcr.use_cassette("weather_get_current_buford.yaml")
def test_get_current_returns_reading() -> None:
    with WeatherClient() as client:
        r = client.get_current(LAT, LON)
    assert isinstance(r, WeatherReading)
    assert r.timestamp.tzinfo == timezone.utc
    assert r.air_temp_c is not None
    assert r.humidity_pct is not None
    assert r.wind_speed_kmh is not None


@_vcr.use_cassette("weather_get_forecast_buford.yaml")
def test_get_forecast_returns_48_hours() -> None:
    with WeatherClient() as client:
        forecasts = client.get_forecast(LAT, LON)
    assert len(forecasts) == 48
    assert all(isinstance(f, HourlyForecast) for f in forecasts)
    assert all(f.timestamp.tzinfo == timezone.utc for f in forecasts)


@_vcr.use_cassette("weather_get_forecast_buford.yaml")
def test_get_forecast_sorted_ascending() -> None:
    with WeatherClient() as client:
        forecasts = client.get_forecast(LAT, LON)
    timestamps = [f.timestamp for f in forecasts]
    assert timestamps == sorted(timestamps)


@_vcr.use_cassette("weather_get_historical_buford.yaml")
def test_get_historical_returns_list() -> None:
    with WeatherClient() as client:
        history = client.get_historical(LAT, LON, start=date(2024, 4, 1), end=date(2024, 4, 2))
    assert len(history) == 48  # 2 days × 24 hours
    assert all(isinstance(h, HourlyForecast) for h in history)
    assert all(h.timestamp.tzinfo == timezone.utc for h in history)


@_vcr.use_cassette("weather_get_historical_buford.yaml")
def test_get_historical_sorted_ascending() -> None:
    with WeatherClient() as client:
        history = client.get_historical(LAT, LON, start=date(2024, 4, 1), end=date(2024, 4, 2))
    timestamps = [h.timestamp for h in history]
    assert timestamps == sorted(timestamps)
