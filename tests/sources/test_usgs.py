"""Unit tests for the USGS NWIS client."""

from __future__ import annotations

import pytest

from stream_conditions.sources.usgs import (
    PARAM_DISCHARGE,
    PARAM_STAGE,
    USGSClient,
    _to_float,
)


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

def _make_payload(
    site_no: str,
    param: str,
    entries: list[tuple[str, str]],
) -> dict:  # type: ignore[type-arg]
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
        "12345678",
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
        "12345678",
        PARAM_DISCHARGE,
        [("2024-01-01T00:00:00.000-00:00", "750")],
    )
    reading = USGSClient()._parse("12345678", payload)[0]
    assert reading.discharge_cfs == pytest.approx(750.0)
    assert reading.site_no == "12345678"


def test_parse_missing_sentinel_becomes_none() -> None:
    payload = _make_payload(
        "12345678",
        PARAM_DISCHARGE,
        [("2024-01-01T00:00:00.000-00:00", "-999999")],
    )
    reading = USGSClient()._parse("12345678", payload)[0]
    assert reading.discharge_cfs is None


def test_parse_merges_parameters() -> None:
    """Discharge and stage readings at the same timestamp should merge into one GaugeReading."""
    site = "12345678"
    dt = "2024-06-01T12:00:00.000-00:00"
    payload = {
        "value": {
            "timeSeries": [
                {
                    "variable": {"variableCode": [{"value": PARAM_DISCHARGE}]},
                    "values": [{"value": [{"dateTime": dt, "value": "400"}]}],
                },
                {
                    "variable": {"variableCode": [{"value": PARAM_STAGE}]},
                    "values": [{"value": [{"dateTime": dt, "value": "3.5"}]}],
                },
            ]
        }
    }
    readings = USGSClient()._parse(site, payload)
    assert len(readings) == 1
    assert readings[0].discharge_cfs == pytest.approx(400.0)
    assert readings[0].stage_ft == pytest.approx(3.5)
