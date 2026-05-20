"""Fly-fishing quality prediction model.

Binary target: session rating >= GOOD_THRESHOLD → "good window" (1), else 0.
A gradient-boosted classifier is used because:
  - it handles the mixed numeric feature types well without scaling requirements
  - partial_fit is not needed at this data scale (retrain from scratch is fast)
  - feature importances are interpretable for the portfolio writeup
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from stream_conditions.features.engineer import FEATURE_COLS, build_feature_matrix

logger = logging.getLogger(__name__)

GOOD_THRESHOLD = 4  # sessions rated 4 or 5 out of 5 are "good" windows


@dataclass
class FishingWindowPredictor:
    """Gradient-boosted binary classifier for optimal fly-fishing window detection.

    Usage::

        predictor = FishingWindowPredictor()
        metrics = predictor.fit(feature_df, ratings_series)
        probas  = predictor.predict_proba(future_feature_df)
        top     = predictor.top_windows(future_feature_df, top_n=5)
    """

    n_estimators: int = 200
    max_depth: int = 4
    learning_rate: float = 0.05
    subsample: float = 0.8
    cv_folds: int = 5
    random_state: int = 42

    _pipeline: Pipeline = field(init=False, repr=False)
    _is_fitted: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._pipeline = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    GradientBoostingClassifier(
                        n_estimators=self.n_estimators,
                        max_depth=self.max_depth,
                        learning_rate=self.learning_rate,
                        subsample=self.subsample,
                        random_state=self.random_state,
                    ),
                ),
            ]
        )

    def fit(self, feature_df: pd.DataFrame, ratings: pd.Series) -> dict[str, Any]:
        """Train the model and return cross-validated performance metrics.

        Args:
            feature_df: Output of ``build_feature_matrix()``; must contain FEATURE_COLS.
            ratings: Integer session ratings aligned with *feature_df* rows (1–5 scale).

        Returns:
            Dict with cv_roc_auc_mean, cv_roc_auc_std, n_train, positive_rate.
        """
        X = feature_df[FEATURE_COLS].fillna(0)
        y = (ratings >= GOOD_THRESHOLD).astype(int)

        cv_scores = cross_val_score(
            self._pipeline, X, y, cv=self.cv_folds, scoring="roc_auc"
        )
        self._pipeline.fit(X, y)
        self._is_fitted = True

        metrics: dict[str, Any] = {
            "cv_roc_auc_mean": float(cv_scores.mean()),
            "cv_roc_auc_std": float(cv_scores.std()),
            "n_train": int(len(X)),
            "positive_rate": float(y.mean()),
        }
        logger.info("Model trained: %s", metrics)
        return metrics

    def predict_proba(self, feature_df: pd.DataFrame) -> np.ndarray:
        """Return P(good window) for each row in *feature_df*."""
        if not self._is_fitted:
            raise RuntimeError("Call fit() before predict_proba().")
        X = feature_df[FEATURE_COLS].fillna(0)
        proba: np.ndarray = self._pipeline.predict_proba(X)[:, 1]
        return proba

    def top_windows(
        self,
        feature_df: pd.DataFrame,
        top_n: int = 5,
    ) -> pd.DataFrame:
        """Return the *top_n* rows with the highest predicted fishing quality."""
        probas = self.predict_proba(feature_df)
        result = feature_df[["datetime_utc"]].copy()
        result["good_window_prob"] = probas
        return result.nlargest(top_n, "good_window_prob").reset_index(drop=True)

    @property
    def feature_importances(self) -> pd.Series:
        """Named feature importances from the underlying GBT (requires fit first)."""
        if not self._is_fitted:
            raise RuntimeError("Call fit() before accessing feature_importances.")
        clf: GradientBoostingClassifier = self._pipeline.named_steps["clf"]
        return pd.Series(clf.feature_importances_, index=FEATURE_COLS).sort_values(
            ascending=False
        )
