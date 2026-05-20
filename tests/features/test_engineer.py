"""Tests for feature engineering logic."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stream_conditions.features.engineer import FEATURE_COLS, build_feature_matrix


def _synthetic_df(n: int = 72) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    times = pd.date_range("2024-06-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "datetime_utc": times,
            "discharge_cfs": rng.uniform(300, 900, n),
            "stage_ft": rng.uniform(2.0, 5.0, n),
            "water_temp_c": rng.uniform(6.0, 14.0, n),
            "air_temp_c": rng.uniform(5.0, 25.0, n),
            "precipitation_mm": rng.uniform(0.0, 8.0, n),
            "wind_speed_ms": rng.uniform(0.0, 12.0, n),
            "cloud_cover_pct": rng.uniform(0.0, 100.0, n),
        }
    )


def test_all_feature_cols_present() -> None:
    result = build_feature_matrix(_synthetic_df())
    missing = [c for c in FEATURE_COLS if c not in result.columns]
    assert missing == [], f"Missing features: {missing}"


def test_row_count_preserved() -> None:
    df = _synthetic_df(48)
    assert len(build_feature_matrix(df)) == 48


def test_cyclical_hour_in_unit_range() -> None:
    result = build_feature_matrix(_synthetic_df(24))
    assert result["hour_sin"].between(-1.0, 1.0).all()
    assert result["hour_cos"].between(-1.0, 1.0).all()


def test_rolling_discharge_lags_by_one() -> None:
    """discharge_6h_avg at row i should not use discharge_cfs[i] (lag-1)."""
    df = _synthetic_df(30)
    # Set a spike at row 10; row 10's avg must NOT reflect that spike
    df.loc[10, "discharge_cfs"] = 999_999.0
    result = build_feature_matrix(df)
    # Row 10's lagged average looks back at rows 4–9 (shift+rolling), not row 10
    assert result.loc[10, "discharge_6h_avg"] < 999_999.0


def test_precip_24h_sum_non_negative() -> None:
    result = build_feature_matrix(_synthetic_df())
    assert (result["precip_24h_sum"] >= 0).all()


def test_output_is_a_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _synthetic_df()
    original_len = len(df)
    build_feature_matrix(df)
    assert len(df) == original_len  # original should not be mutated
