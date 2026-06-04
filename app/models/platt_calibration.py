"""Platt (sigmoid) calibration on model probabilities."""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss


class PlattCalibrator:
    """Maps raw P(home) through a one-feature logistic regression."""

    def __init__(self) -> None:
        self._platt = LogisticRegression(C=1e10, max_iter=1000, random_state=42)

    def fit(self, raw_probs: np.ndarray, y_true: np.ndarray) -> "PlattCalibrator":
        p = np.clip(raw_probs, 1e-6, 1 - 1e-6).reshape(-1, 1)
        self._platt.fit(p, y_true)
        return self

    def transform(self, raw_probs: np.ndarray) -> np.ndarray:
        p = np.clip(raw_probs, 1e-6, 1 - 1e-6).reshape(-1, 1)
        return self._platt.predict_proba(p)[:, 1]

    def fit_transform_eval(
        self,
        raw_train: np.ndarray,
        y_train: np.ndarray,
        raw_test: np.ndarray,
        y_test: np.ndarray,
    ) -> tuple[float, float]:
        self.fit(raw_train, y_train)
        cal_test = self.transform(raw_test)
        cal_test = np.clip(cal_test, 1e-6, 1 - 1e-6)
        return float(log_loss(y_test, cal_test)), float(log_loss(y_train, self.transform(raw_train)))
