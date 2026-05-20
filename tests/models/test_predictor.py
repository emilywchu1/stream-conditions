"""Tests for FishingWindowPredictor."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stream_conditions.features.engineer import build_feature_matrix
from stream_conditions.models.predictor import FishingWindowPredictor


def _make_data(n: int = 120) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(7)
    times = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    raw = pd.DataFrame(
        {
            "datetime_utc": times,
            "discharge_cfs": rng.uniform(300, 900, n),
            "stage_ft": rng.uniform(2.0, 5.0, n),
            "water_temp_c": rng.uniform(5.0, 15.0, n),
            "air_temp_c": rng.uniform(0.0, 28.0, n),
            "precipitation_mm": rng.uniform(0.0, 10.0, n),
            "wind_speed_ms": rng.uniform(0.0, 14.0, n),
            "cloud_cover_pct": rng.uniform(0.0, 100.0, n),
        }
    )
    features = build_feature_matrix(raw)
    ratings = pd.Series(rng.integers(1, 6, n))
    return features, ratings


def test_fit_returns_expected_keys() -> None:
    features, ratings = _make_data()
    metrics = FishingWindowPredictor().fit(features, ratings)
    assert {"cv_roc_auc_mean", "cv_roc_auc_std", "n_train", "positive_rate"} <= metrics.keys()


def test_cv_roc_auc_in_valid_range() -> None:
    features, ratings = _make_data()
    metrics = FishingWindowPredictor().fit(features, ratings)
    assert 0.0 <= metrics["cv_roc_auc_mean"] <= 1.0


def test_predict_proba_shape_and_range() -> None:
    features, ratings = _make_data()
    p = FishingWindowPredictor()
    p.fit(features, ratings)
    proba = p.predict_proba(features)
    assert proba.shape == (len(features),)
    assert np.all(proba >= 0.0) and np.all(proba <= 1.0)


def test_top_windows_length_and_columns() -> None:
    features, ratings = _make_data()
    p = FishingWindowPredictor()
    p.fit(features, ratings)
    top = p.top_windows(features, top_n=4)
    assert len(top) == 4
    assert "good_window_prob" in top.columns
    assert "datetime_utc" in top.columns


def test_top_windows_are_sorted_descending() -> None:
    features, ratings = _make_data()
    p = FishingWindowPredictor()
    p.fit(features, ratings)
    top = p.top_windows(features, top_n=10)
    assert top["good_window_prob"].is_monotonic_decreasing


def test_predict_before_fit_raises() -> None:
    features, _ = _make_data()
    with pytest.raises(RuntimeError, match="fit"):
        FishingWindowPredictor().predict_proba(features)


def test_feature_importances_sum_to_one() -> None:
    features, ratings = _make_data()
    p = FishingWindowPredictor()
    p.fit(features, ratings)
    assert p.feature_importances.sum() == pytest.approx(1.0, abs=1e-6)
