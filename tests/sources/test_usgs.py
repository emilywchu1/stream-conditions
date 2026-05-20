"""Unit tests for the USGS NWIS client."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import vcr as vcrpy

from stream_conditions.sources.usgs import (
    PARAM_DISCHARGE,
    PARAM_GAUGE_HEIGHT,
    USGSClient,
    USGSReading,
    _to_float,
)

CASSETTE_DIR = "tests/sources/cassettes"

_vcr = vcrpy.VCR(cassette_library_dir=CASSETTE_DIR)


# ── _to_float ──────────────────────────────────────────────────────────────────

def test_to_float_valid_string() -> None:
    assert _to_float("550.5") == pytest.approx(550.5)


def test_to_float_integer() -> None:
    assert _to_float(42) == pytest.approx(42.0)


def test_to_float_none_returns_none() -> None:
    assert _to_float(None) is None


def test_to_float_non_numeric_returns_none() -> None:
    assert _to_float("N/A") is None


# ── USGSClient._parse ──────────────────────────────────────────────────────────

def _make_payload(param: str, entries: list[tuple[str, str]]) -> dict:  # type: ignore[type-arg]
    return {
        "value": {
            "timeSeries": [
                {
                    "variable": {"variableCode": [{"value": param}]},
                    "values": [
                        {
                            "value": [
                                {"dateTime": dt, "value": val}
                                for dt, val in entries
                            ]
                        }
                    ],
                }
            ]
        }
    }


def test_parse_returns_correct_count() -> None:
    payload = _make_payload(
        PARAM_DISCHARGE,
        [
            ("2024-01-01T00:00:00.000-00:00", "550"),
            ("2024-01-01T01:00:00.000-00:00", "560"),
        ],
    )
    readings = USGSClient()._parse("12345678", payload)
    assert len(readings) == 2


def test_parse_discharge_value() -> None:
    payload = _make_payload(
        PARAM_DISCHARGE,
        [("2024-01-01T00:00:00.000-00:00", "750")],
    )
    reading = USGSClient()._parse("12345678", payload)[0]
    assert reading.discharge_cfs == pytest.approx(750.0)
    assert reading.site_id == "12345678"


def test_parse_missing_sentinel_becomes_none() -> None:
    payload = _make_payload(
        PARAM_DISCHARGE,
        [("2024-01-01T00:00:00.000-00:00", "-999999")],
    )
    reading = USGSClient()._parse("12345678", payload)[0]
    assert reading.discharge_cfs is None


def test_parse_timestamp_is_utc() -> None:
    payload = _make_payload(
        PARAM_DISCHARGE,
        [("2024-06-01T16:00:00.000-04:00", "400")],
    )
    reading = USGSClient()._parse("12345678", payload)[0]
    assert reading.timestamp == datetime(2024, 6, 1, 20, 0, 0, tzinfo=timezone.utc)


def test_parse_merges_parameters() -> None:
    """Discharge and gauge height at the same timestamp merge into one USGSReading."""
    dt = "2024-06-01T12:00:00.000-00:00"
    payload = {
        "value": {
            "timeSeries": [
                {
                    "variable": {"variableCode": [{"value": PARAM_DISCHARGE}]},
                    "values": [{"value": [{"dateTime": dt, "value": "400"}]}],
                },
                {
                    "variable": {"variableCode": [{"value": PARAM_GAUGE_HEIGHT}]},
                    "values": [{"value": [{"dateTime": dt, "value": "3.5"}]}],
                },
            ]
        }
    }
    readings = USGSClient()._parse("12345678", payload)
    assert len(readings) == 1
    assert readings[0].discharge_cfs == pytest.approx(400.0)
    assert readings[0].gauge_height_ft == pytest.approx(3.5)


def test_parse_unknown_param_goes_to_raw() -> None:
    payload = {
        "value": {
            "timeSeries": [
                {
                    "variable": {"variableCode": [{"value": "99999"}]},
                    "values": [
                        {"value": [{"dateTime": "2024-01-01T00:00:00.000-00:00", "value": "42"}]}
                    ],
                }
            ]
        }
    }
    reading = USGSClient()._parse("12345678", payload)[0]
    assert reading.raw == {"99999": "42"}
    assert reading.discharge_cfs is None


# ── Integration tests via vcrpy ────────────────────────────────────────────────

@_vcr.use_cassette("usgs_get_current_02334430.yaml")
def test_get_current_returns_reading() -> None:
    with USGSClient() as client:
        reading = client.get_current("02334430")
    assert isinstance(reading, USGSReading)
    assert reading.site_id == "02334430"
    assert reading.timestamp.tzinfo == timezone.utc
    assert reading.discharge_cfs is not None
    assert reading.gauge_height_ft is not None


@_vcr.use_cassette("usgs_get_historical_02334430.yaml")
def test_get_historical_returns_list() -> None:
    with USGSClient() as client:
        readings = client.get_historical(
            "02334430",
            start=date(2024, 4, 1),
            end=date(2024, 4, 1),
        )
    assert len(readings) > 0
    assert all(isinstance(r, USGSReading) for r in readings)
    assert all(r.site_id == "02334430" for r in readings)
    assert all(r.timestamp.tzinfo == timezone.utc for r in readings)


@_vcr.use_cassette("usgs_get_historical_02334430.yaml")
def test_get_historical_sorted_ascending() -> None:
    with USGSClient() as client:
        readings = client.get_historical(
            "02334430",
            start=date(2024, 4, 1),
            end=date(2024, 4, 1),
        )
    timestamps = [r.timestamp for r in readings]
    assert timestamps == sorted(timestamps)
