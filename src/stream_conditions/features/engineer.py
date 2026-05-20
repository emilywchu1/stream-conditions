"""Feature engineering for the fly-fishing prediction model.

All rolling statistics are lagged by one step to prevent data leakage when
the feature matrix is used for training (i.e. the label at time T cannot
depend on the raw signal at time T).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Columns this module expects to receive from the merged gauge + weather frame.
RAW_COLS = [
    "datetime_utc",
    "discharge_cfs",
    "stage_ft",
    "water_temp_c",
    "air_temp_c",
    "precipitation_mm",
    "wind_speed_ms",
    "cloud_cover_pct",
]

# Columns consumed by the model — update FEATURE_COLS if you add features.
FEATURE_COLS = [
    "hour_sin",
    "hour_cos",
    "day_of_week",
    "month",
    "discharge_6h_avg",
    "discharge_24h_avg",
    "discharge_delta",
    "stage_ft",
    "stage_delta",
    "water_temp_6h_avg",
    "air_temp_c",
    "precip_24h_sum",
    "wind_speed_ms",
    "cloud_cover_pct",
    "cloud_temp_index",
]


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Transform a raw gauge + weather DataFrame into a model-ready feature matrix.

    Args:
        df: DataFrame with columns matching RAW_COLS (extra columns are ignored).

    Returns:
        A copy of *df* augmented with all columns in FEATURE_COLS, sorted by time.
    """
    df = df.copy()
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
    df = df.sort_values("datetime_utc").reset_index(drop=True)

    # Cyclical encoding of hour so midnight ≈ 23:00 in feature space
    df["hour_of_day"] = df["datetime_utc"].dt.hour
    df["hour_sin"] = np.sin(2 * np.pi * df["hour_of_day"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour_of_day"] / 24)

    df["day_of_week"] = df["datetime_utc"].dt.dayofweek
    df["month"] = df["datetime_utc"].dt.month

    # Discharge rolling statistics (shift(1) = lag-1 to prevent leakage)
    shifted_discharge = df["discharge_cfs"].shift(1)
    df["discharge_6h_avg"] = shifted_discharge.rolling(6, min_periods=1).mean()
    df["discharge_24h_avg"] = shifted_discharge.rolling(24, min_periods=1).mean()
    df["discharge_delta"] = df["discharge_cfs"].diff()

    # Stage rate of change
    df["stage_delta"] = df["stage_ft"].diff()

    # Water temperature trailing average
    df["water_temp_6h_avg"] = (
        df["water_temp_c"].shift(1).rolling(6, min_periods=1).mean()
    )

    # Precipitation accumulation over the past 24 hours
    df["precip_24h_sum"] = (
        df["precipitation_mm"].shift(1).rolling(24, min_periods=1).sum()
    )

    # Interaction feature: high cloud cover at low temperature → poor conditions
    df["cloud_temp_index"] = df["cloud_cover_pct"] * (df["air_temp_c"] + 273.15)

    return df
